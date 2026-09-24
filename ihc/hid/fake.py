"""Simulated CH9329 on a pseudo-terminal, for tests and dry runs without hardware.

    with FakeBackend() as hid:          # a CH9329Backend talking to the simulated chip over a pty
        hid.mouse_rel(10, 0)
        hid.chip.pointer.x               # simulated iPhone pointer

`FakeChip` follows the WCH protocol doc. Behaviour the doc leaves open is marked ASSUMPTION below
and must be revisited against the phase 0 hardware logs.

The pty lets the chip see the baud rate the host configured, so a mismatch behaves like real
hardware: no reply (or, with `noise_on_mismatch`, a few garbage bytes).
"""

from __future__ import annotations

import os
import select
import termios
import threading
import time
import tty
from dataclasses import dataclass, field
from typing import Callable

from ..input import keymap
from . import protocol as p
from .ch9329 import CH9329Backend
from .config import COMMON_BAUDS, ChipConfig

_SPEEDS = {getattr(termios, f"B{b}"): b for b in (1200, 2400, 4800, *COMMON_BAUDS, 230400) if hasattr(termios, f"B{b}")}
_BUTTON_NAMES = {p.MOUSE_LEFT: "left", p.MOUSE_RIGHT: "right", p.MOUSE_MIDDLE: "middle"}

Emit = Callable[..., None]


def _noop(event: str, **fields) -> None:
    pass


@dataclass
class SimPointer:
    """Crude AssistiveTouch pointer: relative moves only, speed-dependent gain, clamped to the screen.

    Each axis moves d * gain * (1 + accel * |d|) points per report, a stand-in for iOS pointer
    acceleration when reports are paced at a fixed interval. Units are iOS points (iPhone 15 portrait).
    """

    width: float = 393.0
    height: float = 852.0
    gain: float = 0.5
    accel: float = 0.03
    x: float = -1.0
    y: float = -1.0
    buttons: int = 0
    emit: Emit = field(default=_noop, repr=False)
    _down_at: dict = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if self.x < 0:
            self.x, self.y = self.width / 2, self.height / 2

    def _step(self, d: int) -> float:
        return d * self.gain * (1 + self.accel * abs(d))

    def relative(self, dx: int, dy: int, buttons: int, wheel: int) -> None:
        self.x = min(max(self.x + self._step(dx), 0.0), self.width - 1)
        self.y = min(max(self.y + self._step(dy), 0.0), self.height - 1)
        self._buttons(buttons)
        if wheel:
            self.emit("scroll", amount=wheel, x=round(self.x, 1), y=round(self.y, 1))

    def _buttons(self, buttons: int) -> None:
        pos = (round(self.x, 1), round(self.y, 1))
        for bit, name in _BUTTON_NAMES.items():
            was, now = self.buttons & bit, buttons & bit
            if now and not was:
                self._down_at[bit] = pos
                self.emit("button_down", button=name, x=pos[0], y=pos[1])
            elif was and not now:
                start = self._down_at.pop(bit, pos)
                if abs(start[0] - pos[0]) + abs(start[1] - pos[1]) < 5:
                    self.emit("click", button=name, x=pos[0], y=pos[1])
                else:
                    self.emit("drag", button=name, x1=start[0], y1=start[1], x2=pos[0], y2=pos[1])
        self.buttons = buttons


@dataclass
class SimKeyboard:
    """Turns keyboard reports into typed text (US layout) and a list of shortcuts."""

    text: str = ""
    shortcuts: list = field(default_factory=list)
    pressed: set = field(default_factory=set)
    leds: int = 0
    emit: Emit = field(default=_noop, repr=False)

    def report(self, mods: int, keys: list[int]) -> None:
        down = [k for k in keys if k]
        for usage in down:
            if usage in self.pressed:
                continue
            ch = keymap.char_for(mods, usage)
            if ch is not None:
                self.text += ch
            elif mods & keymap.SHORTCUT_MODS:
                name = keymap.combo_name(mods, usage)
                self.shortcuts.append(name)
                self.emit("shortcut", combo=name)
            elif usage == keymap.KEYS["backspace"]:
                self.text = self.text[:-1]
            elif usage == keymap.KEYS["capslock"]:
                self.leds ^= 0x02
            else:
                self.emit("key", name=keymap.combo_name(mods, usage))
        self.pressed = set(down)


class FakeChip:
    """Byte-level CH9329 emulator. Feed it host bytes with receive(), get the chip's reply bytes."""

    def __init__(
        self,
        config: ChipConfig | None = None,
        *,
        usb_connected: bool = True,
        version: int = 0x30,
        pointer: SimPointer | None = None,
    ):
        self.stored = config or ChipConfig.factory_default()  # flash contents
        self.active = self.stored  # what the chip booted with
        self.usb_connected = usb_connected
        self.version = version
        self.usb_strings = {0: "", 1: "", 2: ""}
        self.events: list[dict] = []
        self.pointer = pointer or SimPointer()
        self.pointer.emit = self._emit
        self.keyboard = SimKeyboard(emit=self._emit)
        # fault injection
        self.powered = True
        self.noise_on_mismatch = False
        self.drop_next = 0  # silently ignore the next N commands addressed to us
        self.fail_next: list[int] = []  # answer the next commands with these error statuses
        self._buf = bytearray()
        self._lock = threading.Lock()

    @property
    def baud(self) -> int:
        return self.active.get("baud")

    @property
    def address(self) -> int:
        return self.active.get("address")

    @property
    def work_mode(self) -> int:
        return self.active.get("work_mode") & 0x7F

    def power_cycle(self) -> None:
        """Unplug and replug: stored config takes effect (the doc: new config applies at next power-up)."""
        with self._lock:
            self.active = self.stored
            self._buf.clear()
            self.keyboard.pressed.clear()
            self.pointer.buttons = 0
            self._emit("power_cycle", baud=self.baud, address=self.address)

    def _emit(self, event: str, **fields) -> None:
        self.events.append({"t": time.monotonic(), "event": event, **fields})

    def events_of(self, event: str) -> list[dict]:
        return [e for e in self.events if e["event"] == event]

    def receive(self, data: bytes) -> bytes:
        with self._lock:
            self._buf += data
            out = bytearray()
            while True:
                start = self._buf.find(p.HEADER)
                if start < 0:
                    del self._buf[: len(self._buf) - (1 if self._buf[-1:] == p.HEADER[:1] else 0)]
                    break
                del self._buf[:start]
                if len(self._buf) < 5:
                    break
                if self._buf[4] > p.MAX_DATA_LEN:
                    del self._buf[:2]
                    continue
                total = 6 + self._buf[4]
                if len(self._buf) < total:
                    break  # ASSUMPTION: the real chip would answer E1 after its packet interval
                raw = bytes(self._buf[:total])
                del self._buf[:total]
                out += self._handle(raw)
            return bytes(out)

    def _handle(self, raw: bytes) -> bytes:
        addr, cmd = raw[2], raw[3]
        own = self.address
        if not (own == 0 or addr == own or addr == p.BROADCAST_ADDR):
            return b""
        reply = addr != p.BROADCAST_ADDR
        if self.drop_next:
            self.drop_next -= 1
            self._emit("dropped", cmd=cmd)
            return b""
        if p.checksum(raw[:-1]) != raw[-1]:
            status, data = p.Status.BAD_CHECKSUM, None
        elif self.fail_next:
            status, data = self.fail_next.pop(0), None
        else:
            status, data = self._execute(cmd, raw[5:-1])
        if not reply:
            return b""
        if data is not None:
            return p.encode(cmd | p.RESPONSE_OK_FLAG, data, own)
        if status == p.Status.OK:
            return p.encode(cmd | p.RESPONSE_OK_FLAG, bytes([status]), own)
        self._emit("error_reply", cmd=cmd, status=status)
        return p.encode(cmd | p.RESPONSE_ERR_FLAG, bytes([status]), own)

    def _execute(self, cmd: int, d: bytes) -> tuple[int, bytes | None]:
        """Returns (status, data): data is the reply payload for commands that return data."""
        C, S = p.Cmd, p.Status
        if cmd == C.GET_INFO:
            return S.OK, bytes([self.version, int(self.usb_connected), self.keyboard.leds, 0, 0, 0, 0, 0])
        if cmd == C.GET_PARA_CFG:
            return S.OK, self.stored.to_bytes()
        if cmd == C.SET_PARA_CFG:
            if len(d) != 50 or d[0] > 0x03 or d[1] > 0x02:
                return S.BAD_PARAM, None
            self.stored = ChipConfig(d)
            self._emit("config_written", hex=d.hex(" "))
            return S.OK, None
        if cmd == C.SET_DEFAULT_CFG:
            self.stored = ChipConfig.factory_default()
            self.usb_strings = {0: "", 1: "", 2: ""}
            self._emit("config_default")
            return S.OK, None
        if cmd == C.RESET:
            # ASSUMPTION: a software reset re-enumerates USB but does not load the stored config;
            # the datasheet only promises that for a power-up.
            self.keyboard.pressed.clear()
            self.pointer.buttons = 0
            self._emit("reset")
            return S.OK, None
        if cmd == C.GET_USB_STRING:
            if len(d) != 1 or d[0] > 2:
                return S.BAD_PARAM, None
            s = self.usb_strings[d[0]].encode()
            return S.OK, bytes([d[0], len(s)]) + s
        if cmd == C.SET_USB_STRING:
            if len(d) < 2 or d[0] > 2 or d[1] > 23 or len(d) != 2 + d[1]:
                return S.BAD_PARAM, None
            self.usb_strings[d[0]] = d[2:].decode("ascii", errors="replace")
            return S.OK, None
        if cmd in (C.SEND_KB_GENERAL_DATA, C.SEND_KB_MEDIA_DATA, C.SEND_MS_ABS_DATA, C.SEND_MS_REL_DATA):
            return self._hid(cmd, d), None
        return S.BAD_CMD, None

    def _hid(self, cmd: int, d: bytes) -> int:
        C, S = p.Cmd, p.Status
        shape_ok = {
            C.SEND_KB_GENERAL_DATA: len(d) == 8 and d[1] == 0,
            C.SEND_KB_MEDIA_DATA: (len(d) == 2 and d[0] == p.ACPI_REPORT_ID)
            or (len(d) == 4 and d[0] == p.MEDIA_REPORT_ID),
            C.SEND_MS_ABS_DATA: len(d) == 7 and d[0] == 0x02,
            C.SEND_MS_REL_DATA: len(d) == 5 and d[0] == 0x01,
        }[cmd]
        if not shape_ok:
            return S.BAD_PARAM
        # ASSUMPTION: report types missing from the current work mode, and any report while the USB
        # side is not enumerated, fail with E6.
        allowed = {
            C.SEND_KB_GENERAL_DATA: (0, 1),
            C.SEND_KB_MEDIA_DATA: (0,),
            C.SEND_MS_ABS_DATA: (0, 2),
            C.SEND_MS_REL_DATA: (0, 2),
        }[cmd]
        if self.work_mode not in allowed or not self.usb_connected:
            return S.EXEC_ERROR
        if cmd == C.SEND_KB_GENERAL_DATA:
            self.keyboard.report(d[0], list(d[2:]))
        elif cmd == C.SEND_KB_MEDIA_DATA:
            bits = int.from_bytes(d[1:], "little")
            if bits:
                names = p.ACPI_KEYS if d[0] == p.ACPI_REPORT_ID else p.MEDIA_KEYS
                self._emit("media", keys=[n for n, b in names.items() if bits & b])
        elif cmd == C.SEND_MS_REL_DATA:
            dx, dy, wheel = (int.from_bytes(d[i : i + 1], "little", signed=True) for i in (2, 3, 4))
            self.pointer.relative(dx, dy, d[1], wheel)
        else:
            # iOS only follows relative pointers: the simulated phone ignores these.
            x, y = int.from_bytes(d[2:4], "little"), int.from_bytes(d[4:6], "little")
            self._emit("mouse_abs_ignored", x=x, y=y, buttons=d[1])
        return S.OK


class FakeSerialDevice:
    """Runs a FakeChip behind a pty. Open `port` with pyserial like a real /dev/ttyUSB*."""

    def __init__(self, chip: FakeChip, *, simulate_timing: bool = False, processing_s: float = 0.001):
        self.chip = chip
        self.simulate_timing = simulate_timing
        self.processing_s = processing_s
        self._master, self._slave = os.openpty()
        tty.setraw(self._slave)
        self.port = os.ttyname(self._slave)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="fake-ch9329", daemon=True)
        self._thread.start()

    def line_baud(self) -> int | None:
        """Baud rate the host set on the pty."""
        return _SPEEDS.get(termios.tcgetattr(self._slave)[5])

    def _run(self) -> None:
        while not self._stop.is_set():
            ready, _, _ = select.select([self._master], [], [], 0.05)
            if not ready:
                continue
            try:
                data = os.read(self._master, 4096)
            except OSError:
                return
            if not self.chip.powered:
                continue
            baud = self.line_baud()
            if baud != self.chip.baud:
                self.chip._emit("baud_mismatch", host=baud, chip=self.chip.baud, bytes=len(data))
                if self.chip.noise_on_mismatch:
                    self._write(b"\xf0\x0f\xfe")
                continue
            out = self.chip.receive(data)
            if out:
                if self.simulate_timing:
                    time.sleep((len(data) + len(out)) * 10 / baud + self.processing_s)
                self._write(out)

    def _write(self, data: bytes) -> None:
        try:
            os.write(self._master, data)
        except OSError:
            pass

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1)
        for fd in (self._master, self._slave):
            try:
                os.close(fd)
            except OSError:
                pass


class FakeBackend(CH9329Backend):
    """CH9329Backend wired to a FakeChip over a pty. `chip` exposes the simulated device."""

    def __init__(
        self,
        chip: FakeChip | None = None,
        *,
        baud: int | None = None,
        simulate_timing: bool = False,
        **kwargs,
    ):
        self.chip = chip or FakeChip()
        self.device = FakeSerialDevice(self.chip, simulate_timing=simulate_timing)
        try:
            super().__init__(self.device.port, baud or self.chip.baud, **kwargs)
        except Exception:
            self.device.close()
            raise

    def close(self) -> None:
        super().close()
        self.device.close()
