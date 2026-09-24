"""End-to-end check: the repo's unmodified host driver (ihc/hid/ch9329.py) against the firmware's
portable C core running behind a pseudo-terminal (sim_bridge).

    make e2e                      # from firmware/esp32_ble_hid/test_host
    /home/user/iphone-hid/.venv/bin/python e2e_host_driver.py ./build/sim_bridge

Exits non-zero on the first failed check. Uses the public driver API plus transact_raw(); the
SEND_MS_REL_RUN helper also calls the driver's internal _write/_pump to time the single reply
(the driver itself is not modified). The simulated BLE side (link up/down, unsubscribed report
types, full notification buffers, slow buffers, report period) is driven through sim_bridge's
stdin control lines. GET_INFO bytes 6-7 (report period) are checked on the raw payload,
independently of how the driver decodes them.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from ihc.hid import protocol as p  # noqa: E402
from ihc.hid.base import HidStatusError, HidTimeout  # noqa: E402
from ihc.hid.ch9329 import CH9329Backend  # noqa: E402
from ihc.hid.config import ChipConfig  # noqa: E402
from ihc.hid.scan import probe  # noqa: E402

CHECKS = 0

# GET_INFO of the simulator: version 0x40 ("ihc bridge v1.0"), link, LEDs, output 0x7F (simulator),
# collections 0x1F (keyboard, mouse, consumer, system, absolute pointer), features 0x07 (REL_RUN,
# its 0.25 ms interval flag, its E7 "late" status), report period 60 x 0.25 ms = 15 ms
# (u16 little-endian: 3c 00).
INFO_COMPOSITE = "40 01 00 7f 1f 07 3c 00"
INFO_KEYBOARD_ONLY = "40 01 00 7f 01 07 3c 00"
CMD_REL_RUN = 0x30
REL_RUN_QUARTER_MS = 0x80  # flags bit 7: interval in 0.25 ms units
STATUS_RUN_LATE = 0xE7  # vendor: every report delivered, at least one late (not in the driver's enum yet)


def info_period(info: dict) -> int:
    """GET_INFO bytes 6-7 from the raw payload: report period in 0.25 ms units (0 = unknown)."""
    raw = bytes.fromhex(info["raw"])
    check(len(raw) == 8, f"GET_INFO payload length {len(raw)}")
    return int.from_bytes(raw[6:8], "little")


def abs_scale(v: int) -> int:
    """The bridge's 0..4095 -> 0..32767 scaling (ch9329_abs_scale)."""
    return 32767 if v >= 4095 else (v * 32767 + 2047) // 4095


def abs_line(x: int, y: int, buttons: int = 0, wheel: int = 0) -> str:
    sx, sy = abs_scale(x), abs_scale(y)
    b = [buttons & 7, sx & 0xFF, sx >> 8, sy & 0xFF, sy >> 8, wheel & 0xFF]
    return "ABS " + " ".join(f"{v:02X}" for v in b)


def rel_run(hid: CH9329Backend, dx: int, dy: int, count: int, interval_ms: int, buttons: int = 0, listen: float = 3.0):
    """SEND_MS_REL_RUN through the unmodified driver's raw path; returns (frames, seconds to reply)."""
    frame = p.encode(CMD_REL_RUN, bytes([dx & 0xFF, dy & 0xFF, count, interval_ms, buttons]))
    t0 = time.monotonic()
    hid.sync()
    hid._write(frame)  # noqa: SLF001 - transact_raw() would keep listening for the full window
    while True:
        frames = hid._pump(block=True)  # noqa: SLF001
        if frames or time.monotonic() - t0 > listen:
            return frames, time.monotonic() - t0


def check(cond: bool, what: str) -> None:
    global CHECKS
    CHECKS += 1
    if not cond:
        raise SystemExit(f"FAIL: {what}")


class Sim:
    def __init__(self, proc: subprocess.Popen, port: str, events: Path):
        self.proc = proc
        self.port = port
        self.events = events

    def ctl(self, line: str) -> None:
        """Send a control line to the simulated BLE side and wait until it is applied."""
        assert self.proc.stdin is not None and self.proc.stdout is not None
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()
        answer = self.proc.stdout.readline().strip()
        check(answer == f"ok {line}", f"sim control {line!r}: {answer!r}")

    def lines(self) -> list[str]:
        time.sleep(0.05)  # the simulator logs before replying; give the file a moment anyway
        return self.events.read_text().splitlines()

    def reports(self) -> list[str]:
        return [ln for ln in self.lines() if ln.split(" ", 1)[0] in ("KB", "MOUSE", "CONSUMER", "SYSTEM", "ABS")]


@contextmanager
def simulator(binary: str, *extra: str):
    with tempfile.TemporaryDirectory() as tmp:
        events = Path(tmp) / "events.txt"
        events.touch()
        proc = subprocess.Popen(
            [binary, str(events), *extra], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1
        )
        try:
            port = proc.stdout.readline().strip()
            check(port.startswith("/dev/"), f"simulator printed a pty path, got {port!r}")
            yield Sim(proc, port, events)
        finally:
            proc.stdin.close()  # the simulator exits when its stdin closes
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            check(proc.returncode == 0, f"simulator exit code {proc.returncode} (sanitizer report above?)")


def expect_status(fn, status: int, what: str) -> None:
    try:
        fn()
    except HidStatusError as e:
        check(e.status == status, f"{what}: status {e.status:#04x}, expected {status:#04x}")
        return
    check(False, f"{what}: no error, expected status {status:#04x}")


def basic_session(sim: Sim) -> None:
    # The baud scanner finds it at the default rate.
    r = probe(sim.port, 9600)
    check(r.ok, f"scan probe: {r.summary()}")
    # 0x40 identifies the bridge; the unmodified driver shows it as "unknown (0x40)" and works.
    check(r.info["version_raw"] == 0x40 and r.info["usb_connected"], f"probe info {r.info}")

    with CH9329Backend(sim.port, 9600) as hid:
        info = hid.info()
        check(info["raw"] == INFO_COMPOSITE, f"GET_INFO payload {info['raw']}")

        before = len(sim.reports())
        hid.keyboard(0x02, [0x04])  # Shift+A
        hid.keyboard(0, [])
        hid.media(p.MEDIA_KEYS["mute"])
        hid.media(0)
        hid.acpi(p.ACPI_KEYS["power"])
        hid.acpi(0)
        hid.mouse_rel(-3, 5, buttons=p.MOUSE_LEFT, wheel=-1)
        hid.mouse_abs(100, 200)  # absolute pointer report, X/Y scaled to 0..32767
        hid.mouse_abs(4095, 0, buttons=p.MOUSE_RIGHT, wheel=1)
        hid.release_all()
        check(
            sim.reports()[before:]
            == [
                "KB 02 00 04 00 00 00 00 00",
                "KB 00 00 00 00 00 00 00 00",
                "CONSUMER 04 00 00",
                "CONSUMER 00 00 00",
                "SYSTEM 01",
                "SYSTEM 00",
                "MOUSE 01 FD 05 FF",
                abs_line(100, 200),
                abs_line(4095, 0, p.MOUSE_RIGHT, 1),
                "KB 00 00 00 00 00 00 00 00",  # release_all: keyboard, media, power, mouse
                "CONSUMER 00 00 00",
                "SYSTEM 00",
                "MOUSE 00 00 00 00",
            ],
            f"HID reports: {sim.reports()[before:]}",
        )

        # LEDs written by the phone show up in GET_INFO byte 2.
        sim.ctl("leds 2")
        check(hid.info()["caps_lock"], "caps lock LED reported")
        sim.ctl("leds 0")

        # Report period (GET_INFO bytes 6-7, 0.25 ms units, little-endian), asked on every GET_INFO:
        # the 15 ms default, a renegotiated 30 ms BLE interval, USB's 1 ms, a value that needs
        # both bytes, and 0 once the phone is gone.
        check(info_period(hid.info()) == 60, "report period 15 ms by default")
        for units, raw67 in ((120, "78 00"), (4, "04 00"), (0x1234, "34 12"), (0, "00 00"), (60, "3c 00")):
            sim.ctl(f"period {units}")
            info = hid.info()
            check(info["raw"].endswith(raw67) and info_period(info) == units, f"period {units}: {info['raw']}")
            check(info["raw"][:17] == INFO_COMPOSITE[:17], f"period {units} leaves bytes 0-5 alone: {info['raw']}")

        # Configuration block as the host tools see it.
        cfg = hid.get_config()
        check(cfg.warnings() == [], f"config warnings {cfg.warnings()}")
        cfg.validate_for_write()
        check(cfg.get("baud") == 9600 and cfg.get("vid") == 0x1A86 and cfg.get("kb_release_delay_ms") == 1, "defaults")
        for kind in (0, 1, 2):
            check(hid.get_usb_string(kind) == "", f"usb string {kind} empty by default")

        # Invalid writes are refused with E5 and change nothing.
        expect_status(lambda: hid.set_config(cfg.replace(serial_mode=0x01)), p.Status.BAD_PARAM, "serial mode 1")
        expect_status(lambda: hid.set_config(cfg.replace(baud=12345)), p.Status.BAD_PARAM, "odd baud")
        expect_status(lambda: hid.set_config(cfg.replace(work_mode=0x04)), p.Status.BAD_PARAM, "work mode 4")
        check(hid.get_config() == cfg, "config unchanged after refused writes")

        # Keyboard-only profile: write work mode 0x01, RESET applies it (the bridge reboots).
        new, notes = cfg.replace(work_mode=0x01).for_write()
        check(notes == [], f"for_write notes {notes}")
        hid.set_config(new)
        check(hid.get_config() == new, "config read back after SET_PARA_CFG")
        hid.reset()
        time.sleep(0.05)
        check(any(ln.startswith("RESTART work_mode=01") for ln in sim.lines()), "restart into work mode 1")
        hid.keyboard(0x08, [0x2C])  # Cmd+Space still works
        hid.keyboard(0, [])
        check(hid.info()["raw"] == INFO_KEYBOARD_ONLY, "GET_INFO: keyboard collection only")
        expect_status(lambda: hid.mouse_rel(1, 1), p.Status.EXEC_ERROR, "mouse in keyboard-only mode")
        expect_status(lambda: hid.mouse_abs(1, 1), p.Status.EXEC_ERROR, "abs pointer in keyboard-only mode")
        expect_status(lambda: hid.media(p.MEDIA_KEYS["mute"]), p.Status.EXEC_ERROR, "media in keyboard-only mode")
        frames, _ = rel_run(hid, 1, 1, 3, 10)
        check([(f.cmd, f.data) for f in frames] == [(0xF0, b"\xe6")], f"REL_RUN in keyboard-only mode {frames}")

        # Back to the composite profile via factory defaults.
        hid.set_default_config()
        check(hid.get_config() == ChipConfig(cfg.to_bytes()), "SET_DEFAULT_CFG restores the default block")
        hid.reset()
        time.sleep(0.05)
        hid.mouse_rel(1, 0)

        # Protocol errors come back as the WCH status codes.
        bad = bytearray(p.get_info())
        bad[-1] ^= 0x01
        frames = hid.transact_raw(bytes(bad), listen=0.2)
        check([(f.cmd, f.data) for f in frames] == [(0xC1, b"\xe4")], f"bad checksum reply {frames}")
        frames = hid.transact_raw(bytes.fromhex("57 ab 00 02 08 00"), listen=0.2)
        check([(f.cmd, f.data) for f in frames] == [(0xC2, b"\xe1")], f"partial frame reply {frames}")
        frames = hid.transact_raw(p.encode(0x06, b"\x00"), listen=0.2)
        check([(f.cmd, f.data) for f in frames] == [(0xC6, b"\xe3")], f"custom HID reply {frames}")
        frames = hid.transact_raw(b"\x00\xff garbage \x57\x12" + p.get_info(), listen=0.2)
        check([f.cmd for f in frames] == [0x81], f"GET_INFO found behind garbage {frames}")


def reliability_session(sim: Sim) -> None:
    """The bridge must never answer 00 for a report that did not reach the (simulated) phone."""
    with CH9329Backend(sim.port, 9600) as hid:
        # Link lost mid-session: E6, nothing delivered; link back: works again.
        before = len(sim.reports())
        sim.ctl("ready 0")
        check(not hid.info()["usb_connected"], "GET_INFO link status 0 while the link is down")
        expect_status(lambda: hid.keyboard(0, [0x04]), p.Status.EXEC_ERROR, "keyboard while link down")
        expect_status(lambda: hid.mouse_rel(5, 0), p.Status.EXEC_ERROR, "mouse while link down")
        expect_status(lambda: hid.media(p.MEDIA_KEYS["volume_up"]), p.Status.EXEC_ERROR, "media while link down")
        expect_status(lambda: hid.mouse_abs(0, 0), p.Status.EXEC_ERROR, "abs pointer while link down")
        frames, _ = rel_run(hid, 1, 0, 5, 10)
        check([(f.cmd, f.data) for f in frames] == [(0xF0, b"\xe6")], f"REL_RUN while link down {frames}")
        check(len(sim.reports()) == before, "no report delivered while the link was down")
        sim.ctl("ready 1")
        check(hid.info()["usb_connected"], "GET_INFO link status 1 again")
        hid.keyboard(0, [0x04])
        hid.keyboard(0, [])
        check(sim.reports()[before:] == ["KB 00 00 04 00 00 00 00 00", "KB 00 00 00 00 00 00 00 00"], "after relink")

        # Notification buffers stay full once: exactly that command fails, the next one works.
        before = len(sim.reports())
        sim.ctl("fail 1")
        expect_status(lambda: hid.mouse_rel(0, 0, buttons=p.MOUSE_LEFT), p.Status.EXEC_ERROR, "buffers full")
        hid.mouse_rel(0, 0, buttons=p.MOUSE_LEFT)  # the host's retry of the same report
        hid.mouse_rel(0, 0)
        check(sim.reports()[before:] == ["MOUSE 01 00 00 00", "MOUSE 00 00 00 00"], f"retry {sim.reports()[before:]}")

        # A report type the phone did not subscribe to: only that type fails; GET_INFO says 0.
        sim.ctl("reject mouse")
        check(not hid.info()["usb_connected"], "GET_INFO 0 while the mouse report is unsubscribed")
        expect_status(lambda: hid.mouse_rel(1, 0), p.Status.EXEC_ERROR, "unsubscribed mouse report")
        hid.keyboard(0, [])
        sim.ctl("reject none")
        check(hid.info()["usb_connected"], "GET_INFO 1 after the subscription")

        # SEND_MS_REL_RUN: 20 steps 15 ms apart, one reply after the last one.
        before = len(sim.reports())
        frames, took = rel_run(hid, 5, -3, 20, 15, p.MOUSE_LEFT)
        check([(f.cmd, f.data) for f in frames] == [(0xB0, b"\x00")], f"REL_RUN reply {frames}")
        check(sim.reports()[before:] == ["MOUSE 01 05 FD 00"] * 20, f"REL_RUN reports {sim.reports()[before:]}")
        check(0.3 <= took < 0.8, f"REL_RUN took {took * 1000:.0f} ms for 20 slots of 15 ms")
        hid.mouse_rel(0, 0)
        # The host driver's own API: bridge detection and back-to-back runs (a coarse then a fine
        # run, as the pointer model sends them), one write, every reply accounted for.
        info = hid.info()
        check(info["version"] == "ihc bridge v1.0" and info["bridge"]["output"] == "simulator", f"bridge info {info}")
        check(hid.supports_rel_run(), "bridge advertises REL_RUN")
        before = len(sim.reports())
        t0 = time.monotonic()
        hid.mouse_rel_runs([(20, 0, 3), (1, 0, 2)], 20)
        took = time.monotonic() - t0
        check(sim.reports()[before:] == ["MOUSE 00 14 00 00"] * 3 + ["MOUSE 00 01 00 00"] * 2,
              f"chained runs {sim.reports()[before:]}")
        check(0.1 <= took < 0.6, f"chained runs took {took * 1000:.0f} ms for 5 slots of 20 ms")
        for bad in ([5, 0, 0, 10, 0], [5, 0, 2, 10, 8], [5, 0, 255, 9, 0]):
            frames = hid.transact_raw(p.encode(CMD_REL_RUN, bytes(bad)), listen=0.2)
            check([(f.cmd, f.data) for f in frames] == [(0xF0, b"\xe5")], f"REL_RUN {bad}: {frames}")
        # 0.25 ms units (flags bit 7): 12 steps of 90 x 0.25 ms = 22.5 ms (a 7.5 ms BLE link x 3),
        # buttons still in bits 0-2; reserved flag bits 3-6 are refused.
        before = len(sim.reports())
        frames, took = rel_run(hid, 2, 0, 12, 90, REL_RUN_QUARTER_MS | p.MOUSE_LEFT)
        check([(f.cmd, f.data) for f in frames] == [(0xB0, b"\x00")], f"quarter-ms REL_RUN reply {frames}")
        check(sim.reports()[before:] == ["MOUSE 01 02 00 00"] * 12, f"quarter-ms reports {sim.reports()[before:]}")
        check(0.25 <= took < 0.75, f"quarter-ms run took {took * 1000:.0f} ms for 12 slots of 22.5 ms")
        hid.mouse_rel(0, 0)
        for flags in (0x08, 0x40 | REL_RUN_QUARTER_MS):
            frames = hid.transact_raw(p.encode(CMD_REL_RUN, bytes([1, 0, 2, 10, flags])), listen=0.2)
            check([(f.cmd, f.data) for f in frames] == [(0xF0, b"\xe5")], f"REL_RUN flags {flags:#04x}: {frames}")
        # Late reports (the simulated link takes 30 ms per report for a 15 ms pace): the whole move
        # is still played, but the reply is E7 instead of 00, so the host knows the landing point
        # is not exact. The unmodified driver reports it as a failed command with status 0xE7.
        sim.ctl("delay 30")
        before = len(sim.reports())
        frames, _ = rel_run(hid, 1, 0, 3, 15)
        check([(f.cmd, f.data) for f in frames] == [(0xF0, b"\xe7")], f"late REL_RUN reply {frames}")
        check(sim.reports()[before:] == ["MOUSE 00 01 00 00"] * 3, f"late run still complete {sim.reports()[before:]}")
        before = len(sim.reports())
        expect_status(lambda: hid.mouse_rel_runs([(1, 0, 2)], 15), STATUS_RUN_LATE, "driver sees E7 for a late run")
        check(sim.reports()[before:] == ["MOUSE 00 01 00 00"] * 2, f"late driver run complete {sim.reports()[before:]}")
        sim.ctl("delay 0")
        frames, _ = rel_run(hid, 1, 0, 3, 15)
        check([(f.cmd, f.data) for f in frames] == [(0xB0, b"\x00")], f"on-time REL_RUN reply {frames}")
        hid.mouse_rel_runs([(1, 0, 2)], 15)  # on time again: no error
        check(hid.info()["raw"] == INFO_COMPOSITE, "GET_INFO after the late runs")

        # Buffers stay full at step 3 of 5: E6, steps 1-2 delivered, nothing after the failure.
        before = len(sim.reports())
        sim.ctl("failat 3")
        frames, _ = rel_run(hid, 1, 0, 5, 5)
        check([(f.cmd, f.data) for f in frames] == [(0xF0, b"\xe6")], f"REL_RUN with a refused step {frames}")
        check(sim.reports()[before:] == ["MOUSE 00 01 00 00"] * 2, f"run stopped at step 3 {sim.reports()[before:]}")
        hid.mouse_rel(0, 0)  # and the link works normally afterwards

        # Slow buffers (the firmware waits up to 100 ms): still acked well inside the 500 ms timeout.
        sim.ctl("delay 120")
        t0 = time.monotonic()
        hid.mouse_rel(1, 0)
        rtt = time.monotonic() - t0
        check(0.12 <= rtt < 0.45, f"ack after the simulated BLE wait, rtt {rtt * 1000:.0f} ms")
        sim.ctl("delay 0")

    # Fire-and-forget burst with failures in the middle: every failure is reported to the host,
    # every OK report reached the phone, in order.
    before = len(sim.reports())
    with CH9329Backend(sim.port, 9600, wait_ack=False) as hid:
        for i in range(100):
            hid.mouse_rel(i % 50 + 1, 0)
        sim.ctl("fail 7")
        for i in range(100):
            hid.mouse_rel(0, i % 50 + 1)
        hid.sync()
        check(hid.stats["lost_acks"] == 0, f"every command answered {hid.stats}")
        check(
            len(hid.async_errors) == 7 and all(e == (p.Cmd.SEND_MS_REL_DATA, p.Status.EXEC_ERROR) for e in hid.async_errors),
            f"the 7 refused reports were reported as E6: {hid.async_errors}",
        )
    got = sim.reports()[before:]
    want = [f"MOUSE 00 {i % 50 + 1:02X} 00 00" for i in range(100)] + [f"MOUSE 00 00 {i % 50 + 1:02X} 00" for i in range(100)]
    # The 7 failures hit whichever reports the simulator processed right after "fail 7" was
    # applied; the delivered ones must be the sent sequence minus exactly 7, in order.
    check(len(got) == 193, f"193 of 200 reports delivered, got {len(got)}")
    it = iter(want)
    check(all(any(g == w for w in it) for g in got), "delivered reports are an in-order subsequence of the sent ones")

    # Pipelined burst with no failures: all 300 delivered.
    before = len(sim.reports())
    with CH9329Backend(sim.port, 9600, wait_ack=False) as hid:
        for i in range(300):
            hid.mouse_rel(1 if i % 2 else -1, 0)
        hid.sync()
        check(hid.async_errors == [] and hid.stats["lost_acks"] == 0, f"async acks {hid.async_errors} {hid.stats}")
        check(hid.info()["usb_connected"], "info after fire-and-forget burst")
    check(len(sim.reports()) - before == 300, "300 mouse reports forwarded")


def address_session(sim: Sim) -> None:
    with CH9329Backend(sim.port, 9600) as hid:
        hid.set_config(hid.get_config().replace(address=0x05))
        hid.reset()
    time.sleep(0.05)
    with CH9329Backend(sim.port, 9600, addr=0x05) as hid:
        check(hid.info()["usb_connected"], "info at address 0x05")
    with CH9329Backend(sim.port, 9600, addr=0x06, timeout=0.2) as hid:
        try:
            hid.info()
            check(False, "address 0x06 must not be answered")
        except HidTimeout:
            pass
    with CH9329Backend(sim.port, 9600, addr=p.BROADCAST_ADDR) as hid:
        hid.keyboard(0, [0x04])  # broadcast: executed, no ack expected
    with CH9329Backend(sim.port, 9600, addr=0x05) as hid:
        hid.set_default_config()
        hid.reset()
    time.sleep(0.05)
    check("KB 00 00 04 00 00 00 00 00" in sim.lines(), "broadcast keyboard report executed")
    with CH9329Backend(sim.port, 9600) as hid:
        check(hid.get_config().get("address") == 0, "back to address 0x00")


def main(binary: str) -> None:
    with simulator(binary) as sim:
        basic_session(sim)
        reliability_session(sim)
        address_session(sim)

    # No iPhone connected at boot: GET_INFO says so (link 0, report period 0 = unknown) and HID
    # commands fail with E6.
    with simulator(binary, "--not-ready") as sim:
        with CH9329Backend(sim.port, 9600) as hid:
            info = hid.info()
            check(not info["usb_connected"], "link status 0 when not ready")
            check(info_period(info) == 0, f"report period 0 when not connected: {info['raw']}")
            expect_status(lambda: hid.keyboard(0, [0x04]), p.Status.EXEC_ERROR, "keyboard without link")
            expect_status(lambda: hid.mouse_abs(0, 0), p.Status.EXEC_ERROR, "abs pointer without link")
        check(sim.reports() == [], "nothing delivered without a link")

    print(f"e2e: {CHECKS} checks passed against {binary}")


if __name__ == "__main__":
    if len(sys.argv) != 2 or not os.access(sys.argv[1], os.X_OK):
        raise SystemExit("usage: e2e_host_driver.py PATH_TO_sim_bridge")
    main(sys.argv[1])
