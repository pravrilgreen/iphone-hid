"""CH9329 driver over a serial port (pyserial).

Also drives the ESP32 BLE bridge, whose firmware speaks the same frame protocol.

Two send modes:
- wait_ack=True: every command waits for its reply and raises on error or timeout.
- wait_ack=False: HID reports are fire-and-forget. Their replies are read opportunistically and
  counted off; error replies are kept in `async_errors`. The next command that needs a reply
  first waits for the outstanding ones (`sync()`), so a stale ack is never taken for a new reply.

A reply can still come after its command was given up on (timeout, lost ack). The chip answers in
order, so the next exchange first sends GET_INFO and discards everything before its answer: a late
ack is never taken for the ack of a later command.
"""

from __future__ import annotations

import errno
import functools
import threading
import time
from collections import Counter
from typing import Callable, Sequence

import serial

from . import protocol as p
from .base import HidError, HidPortError, HidProtocolError, HidStatusError, HidTimeout
from .config import ChipConfig

Trace = Callable[..., None]

# read() timeout used while polling for replies; a reply is returned as soon as its bytes arrive.
_POLL_S = 0.01
_MAX_KEEP = 256

HID_CMDS = frozenset(
    {p.Cmd.SEND_KB_GENERAL_DATA, p.Cmd.SEND_KB_MEDIA_DATA, p.Cmd.SEND_MS_ABS_DATA, p.Cmd.SEND_MS_REL_DATA}
)

_STATUS_HINTS = {
    # Guess, to be confirmed by the phase 0 logs: the doc does not say what a HID report does
    # while the USB side is not enumerated.
    p.Status.EXEC_ERROR: "for HID reports this likely means the USB side is not enumerated: run `info`"
    " and check usb_connected, that the iPhone is unlocked and the accessory was allowed",
    p.Status.BAD_CHECKSUM: "bytes were corrupted on the serial line (noise, loose wire, baud off)",
    p.Status.BAD_HEADER: "bytes were corrupted on the serial line (noise, loose wire, baud off)",
    p.Status.TIMEOUT: "the chip got a partial frame (bytes lost, or a gap longer than its packet interval)",
    p.Status.BAD_PARAM: "the chip rejected a value (or this report type is disabled in the current work mode)",
    p.Status.RUN_LATE: "the bridge was held up (e.g. waiting for Bluetooth buffers): the distance is not the planned one",
}


def version_string(raw: int) -> str:
    if 0x30 <= raw <= 0x39:
        return f"V1.{raw - 0x30}"
    if p.BRIDGE_VERSION <= raw <= p.BRIDGE_VERSION + 0x0F:
        return f"ihc bridge v1.{raw - p.BRIDGE_VERSION}"
    return f"unknown ({raw:#04x})"


def is_bridge(version_raw: int) -> bool:
    return p.BRIDGE_VERSION <= version_raw <= p.BRIDGE_VERSION + 0x0F


def _cmd_name(cmd: int) -> str:
    try:
        return f"{p.Cmd(cmd).name} ({cmd:#04x})"
    except ValueError:
        return f"{cmd:#04x}"


def _status_name(status: int | None) -> str:
    if status is None:
        return "no status byte"
    try:
        s = p.Status(status)
        return f"{s.name} ({status:#04x}: {p.STATUS_TEXT[s]})"
    except ValueError:
        return f"{status:#04x}"


def _serialized(method):
    """Run under the backend's I/O lock: one command/reply exchange at a time across threads."""

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._io:
            return method(self, *args, **kwargs)

    return wrapper


def status_error(cmd: int, status: int | None) -> HidStatusError:
    """The operator-facing error for a command the chip answered with an error status."""
    hint = _STATUS_HINTS.get(status, "")
    return HidStatusError(f"{_cmd_name(cmd)} failed: {_status_name(status)}" + (f"; {hint}" if hint else ""), cmd, status)


def explain_open_error(port: str, exc: Exception) -> str:
    code = getattr(exc, "errno", None)
    text = str(exc)
    if code == errno.ENOENT or "No such file" in text:
        hint = "the device does not exist. Is the USB-serial end plugged into this host? List candidates with `python -m ihc.hid.scan --list`."
    elif code == errno.EACCES or "Permission denied" in text:
        hint = "permission denied. Run `sudo usermod -aG dialout $USER`, then log out and back in."
    elif code in (errno.EBUSY, errno.EAGAIN, errno.EWOULDBLOCK) or "lock" in text.lower():
        hint = f"the port is in use by another process (another hidtest? ModemManager? check `fuser {port}`)."
    else:
        hint = text
    return f"cannot open {port}: {hint}"


class CH9329Backend:
    def __init__(
        self,
        port: str,
        baud: int = 9600,
        *,
        addr: int = p.DEFAULT_ADDR,
        timeout: float = p.REPLY_TIMEOUT_S,
        wait_ack: bool = True,
        trace: Trace | None = None,
        exclusive: bool = True,
    ):
        self.port = port
        self.baud = baud
        self.addr = addr
        self.timeout = timeout
        self.wait_ack = wait_ack
        self._trace = trace
        self._io = threading.RLock()
        self._parser = p.FrameParser()
        self._inflight: Counter[int] = Counter()  # fire-and-forget commands whose reply is unread
        self._last_send = 0.0
        self._last_rx = 0.0
        self._busy_until = 0.0  # end of the last on-chip run queued (its reply comes after it)
        self._resync = False  # a reply may still be on its way: resync before the next exchange
        self._info: dict | None = None  # last GET_INFO, for capabilities
        self._rx_since_send = bytearray()  # raw bytes seen since the last request, for diagnostics
        self.async_errors: list[tuple[int, int | None]] = []
        self.stats: Counter[str] = Counter()
        try:
            ser = serial.Serial(timeout=_POLL_S, write_timeout=2.0, exclusive=exclusive)
            ser.port, ser.baudrate = port, baud
            # Keep DTR/RTS low: ESP32 dev boards wire them to reset/boot, and asserting them on open
            # (pyserial's default) can reset the bridge into its bootloader.
            ser.dtr = ser.rts = False
            ser.open()
            self._ser = ser
        except (serial.SerialException, OSError) as e:
            raise HidPortError(explain_open_error(port, e)) from e

    # -- context manager ---------------------------------------------------------------------

    def __enter__(self) -> CH9329Backend:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        with self._io:
            if self._ser.is_open:
                self._ser.close()

    # -- HidBackend ----------------------------------------------------------------------------

    def info(self) -> dict:
        """GET_INFO: chip version, USB enumeration state and keyboard LEDs (plus, from the ESP32
        bridge, its output link, HID collections and vendor features)."""
        d = self._request(p.Cmd.GET_INFO).data
        if len(d) < 3:
            raise HidProtocolError(f"GET_INFO reply too short: {d.hex(' ')}")
        info = {
            "version": version_string(d[0]),
            "version_raw": d[0],
            "usb_connected": d[1] == 0x01,
            "usb_status": d[1],
            "num_lock": bool(d[2] & 0x01),
            "caps_lock": bool(d[2] & 0x02),
            "scroll_lock": bool(d[2] & 0x04),
            "raw": d.hex(" "),
        }
        if is_bridge(d[0]) and len(d) >= 6:
            period = int.from_bytes(d[6:8], "little") if len(d) >= 8 else 0
            info["bridge"] = {
                "output": p.BRIDGE_OUTPUTS.get(d[3], f"unknown ({d[3]:#04x})"),
                "collections": [name for bit, name in p.BRIDGE_COLLECTIONS.items() if d[4] & bit],
                "rel_run": bool(d[5] & p.FEATURE_REL_RUN),
                "rel_run_quarter_ms": bool(d[5] & p.FEATURE_REL_RUN_QUARTER_MS),
                "rel_run_late": bool(d[5] & p.FEATURE_REL_RUN_LATE),
                # how often the phone takes a report (BLE connection interval, USB polling), in
                # units of 0.25 ms; None while unknown or not connected
                "report_period_ms": period / 4 if period else None,
            }
        self._info = info
        return info

    def supports_rel_run(self) -> bool | None:
        """Whether the device times relative runs itself (ESP32 bridge). Asked once, then cached;
        None when the device cannot be asked right now (ask again later)."""
        if self._info is None:
            try:
                self.info()
            except HidError:
                return None
        return bool(self._info.get("bridge", {}).get("rel_run"))

    def bridge_feature(self, name: str) -> bool:
        """A feature flag of the bridge's GET_INFO (cached), e.g. "rel_run_quarter_ms"."""
        if self._info is None:
            try:
                self.info()
            except HidError:
                return False
        return bool(self._info.get("bridge", {}).get(name))

    def report_period(self) -> float | None:
        """Seconds between the moments the phone takes a report, when the device knows it (ESP32
        bridge: BLE connection interval or USB polling interval). Asks the device each time: a
        Bluetooth link can renegotiate."""
        try:
            period = self.info().get("bridge", {}).get("report_period_ms")
        except HidError:
            return None
        return period / 1000 if period else None

    def keyboard(self, modifiers: int, keys: Sequence[int]) -> None:
        self._hid(p.kb_general(modifiers, keys, self.addr))

    def media(self, code: int) -> None:
        """Multimedia keys held down, as a protocol.MEDIA_KEYS bitmask; 0 releases."""
        self._hid(p.kb_media(code, self.addr))

    def mouse_rel(self, dx: int, dy: int, buttons: int = 0, wheel: int = 0) -> None:
        self._hid(p.mouse_rel(dx, dy, buttons, wheel, self.addr))

    # -- CH9329 extras -------------------------------------------------------------------------

    def acpi(self, bits: int) -> None:
        """ACPI keys held down, as a protocol.ACPI_KEYS bitmask; 0 releases."""
        self._hid(p.kb_acpi(bits, self.addr))

    def mouse_abs(self, x: int, y: int, buttons: int = 0, wheel: int = 0) -> None:
        """Absolute pointer on a 4096 x 4096 grid (a separate HID report from the relative mouse,
        with its own buttons). Whether iOS follows it is checked per phone by the calibration."""
        self._hid(p.mouse_abs(x, y, buttons, wheel, self.addr))

    @_serialized
    def mouse_rel_runs(self, runs: Sequence[tuple[int, int, int]], interval_ms: float, buttons: int = 0) -> None:
        """ESP32 bridge only: relative runs played on the bridge's clock, one report every
        `interval_ms` (a multiple of 0.25 ms if the bridge supports it, else whole ms), each run
        (dx, dy, count) right after the previous one. All frames go out in one write, so a host
        stall cannot stretch the pace. With wait_ack, returns once every run was delivered (and
        raises on the first failure: E6 undelivered, E7 played off schedule); otherwise the
        replies stay in flight."""
        quarter = abs(interval_ms - round(interval_ms)) > 1e-9
        if quarter and not self.bridge_feature("rel_run_quarter_ms"):
            raise HidError(f"this bridge firmware times runs in whole ms only; {interval_ms} ms cannot be played "
                           "(update the firmware, or pace at a whole number of ms)")
        per_frame = int(p.REL_RUN_MAX_MS // interval_ms) + 1 if interval_ms else 255
        frames, total = [], 0
        for dx, dy, count in runs:
            total += count
            while count > 0:
                n = min(count, per_frame, 255)
                frames.append(p.mouse_rel_run(dx, dy, n, interval_ms, buttons, self.addr, quarter=quarter))
                count -= n
        if not frames:
            return
        broadcast = self.addr == p.BROADCAST_ADDR
        if (self.wait_ack or self._resync) and not broadcast:
            self.sync()
        err0, lost0 = len(self.async_errors), self.stats["lost_acks"]
        self._write(b"".join(frames))
        self._busy_until = max(self._busy_until, self._last_send) + total * interval_ms / 1000
        if broadcast:
            return
        self._inflight[p.CMD_MS_REL_RUN] += len(frames)
        if not self.wait_ack:
            self._pump(block=False)
            return
        self.sync()
        failed = self.async_errors[err0:]
        del self.async_errors[err0:]
        if failed:
            raise status_error(*failed[0])
        if self.stats["lost_acks"] != lost0:
            self.stats["timeouts"] += 1
            raise HidTimeout(f"no reply to {_cmd_name(p.CMD_MS_REL_RUN)} within {self.timeout * 1000:.0f} ms after the run")

    def release_all(self, attempts: int = 3, retry_wait: float = 0.03) -> None:
        """Release every key, media/power key and relative mouse button. Each release is a state, so
        it is simply sent again after a failure (a Bluetooth bridge answers E6 while its buffers are
        full). Every release is tried; afterwards the first failure is raised, so the caller knows
        something may still be held. (An absolute pointer button is released by a report at its
        position: see PointerModel.release_all.)"""
        frames = (
            p.kb_general(0, [], self.addr),
            p.kb_media(0, self.addr),
            p.kb_acpi(0, self.addr),
            p.mouse_rel(0, 0, 0, 0, self.addr),
        )
        failed: HidError | None = None
        for frame in frames:
            for attempt in range(attempts):
                try:
                    self._hid(frame)
                    break
                except HidPortError:
                    raise
                except HidStatusError as e:
                    if e.status in (p.Status.BAD_PARAM, p.Status.BAD_CMD):  # not in this work mode: nothing to release
                        break
                    err = e
                except HidError as e:
                    err = e
                if attempt == attempts - 1:
                    failed = failed or err
                else:
                    time.sleep(retry_wait)
        if failed is not None:
            raise failed

    def get_config(self) -> ChipConfig:
        d = self._request(p.Cmd.GET_PARA_CFG).data
        if len(d) != 50:
            raise HidProtocolError(f"GET_PARA_CFG returned {len(d)} bytes, expected 50: {d.hex(' ')}")
        return ChipConfig(d)

    def set_config(self, cfg: ChipConfig) -> None:
        """Write the 50-byte block as-is. Callers must show a diff and get confirmation first;
        see ChipConfig.for_write(). Takes effect at the next power-up."""
        self._request_status(p.Cmd.SET_PARA_CFG, cfg.to_bytes())

    def set_default_config(self) -> None:
        """Restore factory parameters and USB strings (effective at the next power-up)."""
        self._request_status(p.Cmd.SET_DEFAULT_CFG)

    def reset(self) -> None:
        """Software reset. The chip drops off USB and re-enumerates."""
        self._request_status(p.Cmd.RESET)
        self._parser.reset()
        self._info = None

    def get_usb_string(self, kind: int) -> str:
        """kind: 0 vendor, 1 product, 2 serial number."""
        d = self._request(p.Cmd.GET_USB_STRING, bytes([kind])).data
        if len(d) < 2 or d[0] != kind:
            raise HidProtocolError(f"unexpected GET_USB_STRING reply: {d.hex(' ')}")
        return d[2 : 2 + d[1]].decode("ascii", errors="replace")

    @_serialized
    def transact_raw(self, frame: bytes, listen: float = 0.3) -> list[p.Frame]:
        """Write bytes verbatim (for experiments) and return every frame seen for `listen` seconds."""
        self.sync()
        self._write(frame)
        frames: list[p.Frame] = []
        deadline = time.monotonic() + listen
        while time.monotonic() < deadline:
            frames += self._pump(block=True)
        return frames

    # -- plumbing ------------------------------------------------------------------------------

    @_serialized
    def sync(self) -> None:
        """Wait for replies to fire-and-forget commands, then drop anything left in the input.
        After a reply was given up on, first make sure it cannot arrive later (see _resync_now)."""
        if self._inflight:
            # replies trail their commands; keep waiting while they are still arriving
            while self._inflight and time.monotonic() < max(self._last_send, self._last_rx, self._busy_until) + self.timeout:
                self._pump(block=True)
            self._pump(block=False)
            if self._inflight:
                lost = sum(self._inflight.values())
                self.stats["lost_acks"] += lost
                self._emit("lost_acks", count=lost, cmds={f"{c:#04x}": n for c, n in self._inflight.items()})
                self._inflight.clear()
                self._resync = True
        if self._resync and self.addr != p.BROADCAST_ADDR:
            self._resync_now()
        self._pump(block=False)
        self._parser.reset()

    def _resync_now(self) -> None:
        """Send GET_INFO and discard every frame before its reply. The chip answers in order, so
        once it answers, no reply to an earlier command can still arrive."""
        late = len(self._pump(block=False))
        self._write(p.get_info(self.addr))
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            for f in self._pump(block=True):
                if f.request_cmd == p.Cmd.GET_INFO:
                    self._resync = False
                    self.stats["late_replies"] += late
                    self._emit("resynced", discarded=late)
                    return
                late += 1
        self.stats["late_replies"] += late
        self.stats["timeouts"] += 1
        raise HidTimeout(f"{self.port} stopped answering: no reply to GET_INFO within {self.timeout * 1000:.0f} ms "
                         "while resynchronising after a lost reply")

    @_serialized
    def _hid(self, frame: bytes) -> None:
        if self.wait_ack and frame[2] != p.BROADCAST_ADDR:
            self._check(self._roundtrip(frame), frame[3])
            return
        if self._resync:
            self.sync()
        self._write(frame)
        if frame[2] != p.BROADCAST_ADDR:
            self._inflight[frame[3]] += 1
        self._pump(block=False)

    @_serialized
    def _request(self, cmd: int, data: bytes = b"") -> p.Frame:
        if self.addr == p.BROADCAST_ADDR:
            raise HidError("address 0xFF is broadcast: the chip never replies, so requests cannot work")
        reply = self._roundtrip(p.encode(cmd, data, self.addr))
        if reply.is_error:
            self._check(reply, cmd)
        return reply

    def _request_status(self, cmd: int, data: bytes = b"") -> None:
        self._check(self._request(cmd, data), cmd)

    def _roundtrip(self, frame: bytes) -> p.Frame:
        cmd = frame[3]
        self.sync()
        discarded0 = self._parser.discarded
        self._rx_since_send.clear()
        t0 = time.monotonic()
        self._write(frame)
        deadline = t0 + self.timeout
        block = False
        while True:
            for f in self._pump(block=block):
                if f.request_cmd == cmd:
                    self._emit("reply", cmd=f"{cmd:#04x}", rtt_ms=round((time.monotonic() - t0) * 1000, 2))
                    return f
            if time.monotonic() >= deadline:
                break
            block = True
        garbage = self._parser.discarded - discarded0
        received = bytes(self._rx_since_send)
        self.stats["timeouts"] += 1
        self._resync = True  # its reply may still come: never take it for the next one
        self._emit("timeout", cmd=f"{cmd:#04x}", received=received.hex(" "), garbage=garbage)
        raise HidTimeout(self._timeout_message(cmd, received, garbage), received)

    def _timeout_message(self, cmd: int, received: bytes, garbage: int) -> str:
        head = f"no reply to {_cmd_name(cmd)} within {self.timeout * 1000:.0f} ms on {self.port} @ {self.baud} baud"
        if garbage:
            return (
                f"{head}; got {garbage} byte(s) that are not valid frames ({received[:32].hex(' ')}). "
                "The baud rate is probably wrong: run `python -m ihc.hid.scan --port PORT`."
            )
        if received:
            return f"{head}; got an incomplete frame ({received.hex(' ')}). Bytes lost on the line?"
        return (
            f"{head}; nothing came back. Check: (1) the HID end of the cable is plugged into the "
            "iPhone/hub/host, since the CH9329 is usually powered from that side; (2) PORT is the "
            "CH9329 cable; (3) TX/RX wiring on bare modules; (4) the baud rate "
            "(`python -m ihc.hid.scan`); (5) the chip address if it was ever changed (`--addr`)."
        )

    def _check(self, reply: p.Frame, cmd: int) -> None:
        status = reply.status
        if not reply.is_error and status == p.Status.OK:
            return
        self.stats["errors"] += 1
        if not reply.is_error and status is None:
            raise HidProtocolError(f"{_cmd_name(cmd)}: unexpected reply {reply.data.hex(' ')}")
        raise status_error(cmd, status)

    def _write(self, frame: bytes) -> None:
        try:
            self._ser.write(frame)
            self._ser.flush()  # return once the bytes are on the wire, so pacing is real
        except (serial.SerialException, OSError) as e:
            raise HidPortError(f"write to {self.port} failed ({e}); was the cable unplugged?") from e
        self._last_send = time.monotonic()
        self.stats["tx"] += 1
        self._emit("tx", hex=frame.hex(" "))

    def _pump(self, block: bool) -> list[p.Frame]:
        """Read what is available (blocking up to _POLL_S if `block`), parse it, and account for
        replies to fire-and-forget commands. Returns the frames that were not such replies."""
        try:
            n = self._ser.in_waiting
            chunk = self._ser.read(n or 1) if (n or block) else b""
        except (serial.SerialException, OSError) as e:
            raise HidPortError(f"read from {self.port} failed ({e}); was the cable unplugged?") from e
        if not chunk:
            return []
        self._last_rx = time.monotonic()
        if len(self._rx_since_send) < _MAX_KEEP:
            self._rx_since_send += chunk
        out = []
        for f in self._parser.feed(chunk):
            self.stats["rx"] += 1
            self._emit("rx", hex=f.to_bytes().hex(" "))
            cmd = f.request_cmd
            if f.cmd != p.CHIP_HID_DATA and self._inflight[cmd] > 0:
                self._inflight[cmd] -= 1
                if self._inflight[cmd] == 0:
                    del self._inflight[cmd]
                if f.is_error or f.status != p.Status.OK:
                    self.async_errors.append((cmd, f.status))
                    self.stats["errors"] += 1
                    self._emit("async_error", cmd=f"{cmd:#04x}", status=f.status)
                continue
            out.append(f)
        return out

    def _emit(self, event: str, **fields) -> None:
        if self._trace is not None:
            self._trace(event, **fields)
