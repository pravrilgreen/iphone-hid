import time

import pytest

from ihc.hid import protocol as p
from ihc.hid.base import HidError, HidPortError, HidStatusError, HidTimeout
from ihc.hid.ch9329 import CH9329Backend
from ihc.hid.config import ChipConfig
from ihc.hid.fake import FakeBackend, FakeChip, FakeSerialDevice
from ihc.input import keymap


def test_info(hid):
    info = hid.info()
    assert info["version"] == "V1.0"
    assert info["usb_connected"] is True
    assert info["raw"] == "30 01 00 00 00 00 00 00"


def test_typing_reaches_the_simulated_phone(hid):
    for mods, keys in keymap.text_reports("Hi there, 42!\n"):
        hid.keyboard(mods, keys)
    assert hid.chip.keyboard.text == "Hi there, 42!\n"


def test_shortcut_is_recorded(hid):
    hid.keyboard(*keymap.parse_combo("cmd+space"))
    hid.keyboard(0, [])
    assert hid.chip.keyboard.shortcuts == ["cmd+space"]


def test_relative_moves_and_clamping(hid):
    pt = hid.chip.pointer
    x0 = pt.x
    hid.mouse_rel(10, 0)
    assert pt.x > x0
    for _ in range(20):
        hid.mouse_rel(-127, -127)
    assert (pt.x, pt.y) == (0.0, 0.0)


def test_click_and_drag_events(hid):
    hid.mouse_rel(0, 0, buttons=p.MOUSE_LEFT)
    hid.mouse_rel(0, 0, buttons=0)
    hid.mouse_rel(0, 0, buttons=p.MOUSE_LEFT)
    hid.mouse_rel(60, 0, buttons=p.MOUSE_LEFT)
    hid.mouse_rel(0, 0, buttons=0)
    assert [e["event"] for e in hid.chip.events if e["event"] in ("click", "drag")] == ["click", "drag"]


def test_absolute_reports_are_acked_but_ignored_by_the_phone(hid):
    x0 = hid.chip.pointer.x
    hid.mouse_abs(0, 0)
    assert hid.chip.pointer.x == x0
    assert hid.chip.events_of("mouse_abs_ignored")


def test_media_and_acpi(hid):
    hid.media(p.MEDIA_KEYS["volume_up"])
    hid.media(0)
    hid.acpi(p.ACPI_KEYS["sleep"])
    hid.acpi(0)
    assert [e["keys"] for e in hid.chip.events_of("media")] == [["volume_up"], ["sleep"]]


def test_fire_and_forget_then_request_skips_stale_acks(hid):
    hid.wait_ack = False
    for _ in range(40):
        hid.mouse_rel(1, 0)
    assert hid.info()["version"] == "V1.0"  # reply matched to GET_INFO, not a stale 0x85
    assert not hid._inflight
    assert hid.stats["lost_acks"] == 0
    hid.wait_ack = True
    hid.mouse_rel(1, 0)


def test_fire_and_forget_errors_are_collected(hid):
    hid.wait_ack = False
    hid.chip.fail_next = [p.Status.EXEC_ERROR]
    hid.mouse_rel(1, 0)
    hid.sync()
    assert hid.async_errors == [(p.Cmd.SEND_MS_REL_DATA, p.Status.EXEC_ERROR)]


def test_status_error_has_hint(hid):
    hid.chip.usb_connected = False
    with pytest.raises(HidStatusError, match="USB side is not enumerated") as e:
        hid.mouse_rel(1, 0)
    assert e.value.status == p.Status.EXEC_ERROR
    hid.chip.usb_connected = True
    hid.mouse_rel(1, 0)


def test_timeout_when_chip_unpowered(hid):
    hid.chip.powered = False
    t0 = time.monotonic()
    with pytest.raises(HidTimeout, match="nothing came back") as e:
        hid.info()
    assert 0.25 < time.monotonic() - t0 < 1.0
    assert e.value.received == b""
    hid.chip.powered = True
    assert hid.info()["usb_connected"]


def test_recovers_after_a_dropped_command(hid):
    hid.chip.drop_next = 1
    with pytest.raises(HidTimeout):
        hid.mouse_rel(1, 0)
    hid.mouse_rel(1, 0)
    assert hid.stats["timeouts"] == 1


def test_wrong_baud_reports_garbage(chip_backend):
    hid = chip_backend(baud=115200)
    hid.chip.noise_on_mismatch = True
    with pytest.raises(HidTimeout, match="baud rate is probably wrong") as e:
        hid.info()
    assert e.value.received


def test_garbage_before_reply_is_skipped(hid):
    hid.device._write(b"\x00\x57\x12\xff")
    assert hid.info()["version"] == "V1.0"


def test_bad_checksum_gets_error_reply(hid):
    frames = hid.transact_raw(bytes.fromhex("57 ab 00 01 00 04"))
    assert len(frames) == 1 and frames[0].is_error and frames[0].status == p.Status.BAD_CHECKSUM


def test_config_roundtrip_applies_at_power_cycle():
    chip = FakeChip()
    dev = FakeSerialDevice(chip)
    try:
        with CH9329Backend(dev.port, 9600, timeout=0.3) as hid:
            cfg = hid.get_config()
            assert cfg == ChipConfig.factory_default()
            new, _ = cfg.replace(baud=115200).for_write()
            hid.set_config(new)
            assert hid.get_config() == new
            assert chip.baud == 9600  # stored in flash, not active until power-up
            chip.power_cycle()
            with pytest.raises(HidTimeout):
                hid.info()
        with CH9329Backend(dev.port, 115200, timeout=0.3) as hid:
            assert hid.info()["usb_connected"]
            hid.set_default_config()
        chip.power_cycle()
        with CH9329Backend(dev.port, 9600, timeout=0.3) as hid:
            assert hid.info()["version"] == "V1.0"
    finally:
        dev.close()


def test_chip_rejects_pin_modes_on_write(hid):
    with pytest.raises(HidStatusError, match="BAD_PARAM"):
        hid.set_config(ChipConfig.factory_default())


def test_keyboard_only_mode_rejects_mouse(chip_backend):
    chip = FakeChip(ChipConfig.factory_default().replace(work_mode=0x01))
    hid = chip_backend(chip)
    hid.keyboard(0, [0x04])
    with pytest.raises(HidStatusError):
        hid.mouse_rel(1, 0)


def test_addressing(chip_backend):
    chip = FakeChip(ChipConfig.factory_default().replace(address=0x05))
    hid = chip_backend(chip, timeout=0.2)
    with pytest.raises(HidTimeout):
        hid.info()
    hid.addr = 0x05
    assert hid.info()["usb_connected"]
    hid.addr = p.BROADCAST_ADDR
    with pytest.raises(HidError, match="broadcast"):
        hid.info()
    hid.mouse_rel(0, 0, buttons=p.MOUSE_LEFT)  # executed, never answered
    hid.mouse_rel(0, 0)
    hid.addr = 0x05
    hid.info()  # replies come in order, so the broadcast frames were handled before this one
    assert hid.chip.events_of("click")


def test_usb_strings_and_reset(hid):
    assert hid.get_usb_string(1) == ""
    hid.chip.usb_strings[1] = "HID bridge"
    assert hid.get_usb_string(1) == "HID bridge"
    hid.reset()
    assert hid.chip.events_of("reset")


def test_release_all(hid):
    hid.keyboard(0, [0x04])
    hid.mouse_rel(0, 0, buttons=p.MOUSE_LEFT)
    hid.release_all()
    assert hid.chip.pointer.buttons == 0 and not hid.chip.keyboard.pressed


def test_trace_sees_frames():
    events = []
    with FakeBackend(trace=lambda e, **f: events.append((e, f))) as hid:
        hid.info()
    kinds = [e for e, _ in events]
    assert kinds[:3] == ["tx", "rx", "reply"]
    assert events[0][1]["hex"] == "57 ab 00 01 00 03"


def test_open_errors_are_explained():
    with pytest.raises(HidPortError, match="does not exist"):
        CH9329Backend("/dev/ttyDOESNOTEXIST")


def test_port_busy_is_explained():
    chip = FakeChip()
    dev = FakeSerialDevice(chip)
    try:
        with CH9329Backend(dev.port):
            with pytest.raises(HidPortError, match="in use"):
                CH9329Backend(dev.port)
    finally:
        dev.close()


def test_simulated_timing_is_close_to_9600_baud():
    with FakeBackend(simulate_timing=True) as hid:
        t0 = time.monotonic()
        for _ in range(10):
            hid.mouse_rel(0, 0)
        per_report = (time.monotonic() - t0) / 10
    assert 0.018 < per_report < 0.040  # 18 bytes on the wire at 9600 baud ≈ 18.75 ms


class _ScriptedSerial:
    """Serial stand-in whose reply shows up in full exactly when the driver does a blocking read."""

    def __init__(self, *args, **kwargs):
        self.is_open = True
        self._rx = bytearray()
        self._armed = b""
        self._checks = 0

    def write(self, data):
        frame = p.decode(bytes(data))
        self._armed = p.encode(frame.cmd | 0x80, bytes(8) if frame.cmd == p.Cmd.GET_INFO else b"\x00")
        self._checks = 0

    def flush(self):
        pass

    @property
    def in_waiting(self):
        self._checks += 1
        if self._armed and self._checks > 1:
            self._rx += self._armed
            self._armed = b""
        return len(self._rx)

    def read(self, n=1):
        data = bytes(self._rx[:n])
        del self._rx[:n]
        return data

    def close(self):
        self.is_open = False


def test_reply_read_by_blocking_poll_is_not_dropped(monkeypatch):
    import ihc.hid.ch9329 as ch

    monkeypatch.setattr(ch.serial, "Serial", _ScriptedSerial)
    with CH9329Backend("/dev/null", timeout=0.2) as hid:
        hid.mouse_rel(1, 0)
        assert hid.info()["version_raw"] == 0


def test_late_reply_is_never_taken_for_the_next_ack(chip_backend):
    """The press executes but its ack comes after the timeout. The next exchange must not count
    that ack as its own: here the release is dropped by the chip, so it has to fail."""
    chip = FakeChip()
    hid = chip_backend(chip, timeout=0.1)
    chip.reply_delays = [0.25]
    with pytest.raises(HidTimeout):
        hid.keyboard(*keymap.parse_combo("cmd+space"))
    time.sleep(0.2)  # the late ack is now waiting in the input
    receive = chip.receive

    def lose_keyboard_frames(data: bytes) -> bytes:
        return b"" if data[3:4] == bytes([p.Cmd.SEND_KB_GENERAL_DATA]) else receive(data)

    chip.receive = lose_keyboard_frames
    with pytest.raises(HidTimeout):
        hid.keyboard(0, [])
    chip.receive = receive
    assert chip.keyboard.pressed == {0x2C}  # the release never arrived: reported, not hidden
    hid.keyboard(0, [])
    assert not chip.keyboard.pressed
    assert hid.stats["late_replies"] >= 1


def test_keyboard_resends_after_a_late_ack_and_releases(chip_backend):
    from ihc.input.keyboard import Keyboard

    chip = FakeChip()
    hid = chip_backend(chip, timeout=0.1)
    kb = Keyboard(hid)
    chip.reply_delays = [0.2]
    kb.key("cmd+space")
    assert kb.resends == 1 and not chip.keyboard.pressed
    assert chip.keyboard.shortcuts == ["cmd+space"]


def test_keyboard_raises_when_the_final_release_fails(chip_backend):
    from ihc.input.keyboard import Keyboard

    chip = FakeChip()
    hid = chip_backend(chip, timeout=0.1)
    kb = Keyboard(hid, retries=0)
    original = hid.keyboard

    def no_release(mods, keys):
        if not keys:
            raise HidTimeout("release lost")
        original(mods, keys)

    hid.keyboard = no_release
    with pytest.raises(HidTimeout):
        kb.key("cmd+space")
    with pytest.raises(HidTimeout):
        kb.type("ab")
    assert kb.typed == 0


def test_executed_but_unanswered_command(chip_backend):
    chip = FakeChip()
    hid = chip_backend(chip, timeout=0.1)
    chip.mute_next = 1
    with pytest.raises(HidTimeout):
        hid.mouse_rel(5, 0)
    assert chip.pointer.history[-1][0] > 0  # it moved
    hid.mouse_rel(0, 0)  # resynchronised, next exchange is normal
    assert hid.info()["usb_connected"]


def test_release_all_includes_media_and_power_keys(hid):
    hid.release_all()
    frames = [e for e in hid.chip.events if e["event"] == "error_reply"]
    assert not frames
    assert hid.stats["tx"] >= 4


def test_bridge_info_and_capabilities(chip_backend):
    plain = chip_backend(FakeChip())
    assert plain.supports_rel_run() is False and "bridge" not in plain.info()
    bridge = chip_backend(FakeChip(bridge=True))
    info = bridge.info()
    assert info["version"] == "ihc bridge v1.0"
    assert info["bridge"] == {"output": "simulator", "rel_run": True, "report_period_ms": None,
                              "collections": ["keyboard", "mouse", "consumer", "system", "absolute"]}
    assert bridge.supports_rel_run() is True


def test_bridge_runs_are_timed_on_the_chip(chip_backend):
    chip = FakeChip(bridge=True)
    hid = chip_backend(chip, timeout=0.3)
    t0 = time.monotonic()
    hid.mouse_rel_runs([(5, 0, 4), (1, 0, 3)], 20)
    took = time.monotonic() - t0
    assert took >= 0.14  # 7 slots of 20 ms before the last reply
    times = [t for t, _, _ in list(chip.pointer.history)[-7:]]
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert all(abs(g - 0.02) < 0.006 for g in gaps), gaps  # one schedule across both runs
    # fire-and-forget: the acks come after the run, and are still all accounted for
    hid.wait_ack = False
    hid.mouse_rel_runs([(0, 3, 10)], 20)
    hid.sync()
    assert hid.stats["lost_acks"] == 0 and not hid.async_errors


def test_bridge_run_refused_in_keyboard_only_mode(chip_backend):
    chip = FakeChip(ChipConfig.factory_default().replace(work_mode=0x01), bridge=True)
    hid = chip_backend(chip)
    with pytest.raises(HidStatusError) as e:
        hid.mouse_rel_runs([(1, 0, 3)], 10)
    assert e.value.status == p.Status.EXEC_ERROR
    assert "mouse" not in hid.info()["bridge"]["collections"]


def test_plain_ch9329_rejects_runs(hid):
    with pytest.raises(HidStatusError) as e:
        hid.mouse_rel_runs([(1, 0, 3)], 10)
    assert e.value.status == p.Status.BAD_CMD


def test_bridge_reports_its_link_period(chip_backend):
    chip = FakeChip(bridge=True)
    chip.link_period = 0.015
    hid = chip_backend(chip)
    assert hid.info()["bridge"]["report_period_ms"] == 15.0
    assert hid.report_period() == 0.015
    chip.usb_connected = False  # not connected: unknown
    assert hid.report_period() is None
