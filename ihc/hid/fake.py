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

import math
import os
import select
from collections import deque
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
    """Stand-in for the AssistiveTouch pointer: relative moves only, velocity-dependent gain,
    clamped to the screen. Units are iOS points (default: iPhone 15 portrait, 393 x 852 pt).

    Per axis a report of d units moves d * gain * (1 + accel * v / 1000) points, where v is the
    report's speed in units/s: d over the time since the previous report, a gap counted as at most
    `idle_reset` seconds (the velocity estimate forgets after that, as mouse drivers do). Real iOS
    acceleration is unknown in detail but also speed-based, which is what the pacer relies on: a
    fixed step at a fixed interval, started from rest, covers a fixed distance. `tracking` scales
    everything like the iOS Tracking Speed slider.
    """

    width: float = 393.0
    height: float = 852.0
    gain: float = 0.35
    accel: float = 1.5
    tracking: float = 1.0
    idle_reset: float = 0.1
    # Absolute reports: iOS support is unverified (the Aiden project reports it works over USB), so
    # it is a switch. When on, the 0..4095 range spans the screen and the cursor glides to the new
    # spot over `abs_glide` seconds, so a click sent too early lands short.
    absolute: bool = False
    abs_glide: float = 0.06
    x: float = -1.0
    y: float = -1.0
    buttons: int = 0
    clock: Callable[[], float] = field(default=time.monotonic, repr=False)
    emit: Emit = field(default=_noop, repr=False)
    history: deque = field(default_factory=lambda: deque(maxlen=512), repr=False)
    _down_at: dict = field(default_factory=dict, repr=False)
    _last_t: float = field(default=0.0, repr=False)
    _glide: tuple | None = field(default=None, repr=False)
    # The relative and the absolute pointer are separate HID reports, each with its own buttons: a
    # button pressed through one is released only through the same one. iOS sees their union.
    _rel_buttons: int = field(default=0, repr=False)
    _abs_buttons: int = field(default=0, repr=False)
    # When the report reached the phone, if the chip knows it better than clock() at processing
    # time (the pty rig: when its bytes crossed the line, not when a Python thread got to them).
    at: float | None = field(default=None, repr=False)

    def _now(self) -> float:
        return self.clock() if self.at is None else self.at

    def __post_init__(self) -> None:
        if self.x < 0:
            self.x, self.y = self.width / 2, self.height / 2
        self.history.append((self.clock(), self.x, self.y))

    def _step(self, d: int, dt: float) -> float:
        speed = abs(d) / max(dt, 0.008)
        return d * self.gain * self.tracking * (1 + self.accel * speed / 1000)

    def _settle_glide(self, now: float) -> None:
        if self._glide is not None:
            t0, x0, y0, x1, y1 = self._glide
            k = min(1.0, (now - t0) / self.abs_glide) if self.abs_glide > 0 else 1.0
            self.x, self.y = x0 + (x1 - x0) * k, y0 + (y1 - y0) * k
            if k >= 1.0:
                self._glide = None

    def absolute_report(self, ax: int, ay: int, buttons: int, wheel: int) -> None:
        now = self._now()
        self._settle_glide(now)
        tx = min(max(ax / 4095 * self.width, 0.0), self.width - 1)
        ty = min(max(ay / 4095 * self.height, 0.0), self.height - 1)
        if (tx, ty) != (self.x, self.y):
            self._glide = (now, self.x, self.y, tx, ty)
            self.history.append((now + self.abs_glide, tx, ty))
        self._abs_buttons = buttons
        self._buttons(self._rel_buttons | buttons, now)
        if wheel:
            self.emit("scroll", amount=wheel, x=round(self.x, 1), y=round(self.y, 1))

    def relative(self, dx: int, dy: int, buttons: int, wheel: int) -> None:
        now = self._now()
        self._settle_glide(now)
        if dx or dy:  # velocity comes from motion reports only
            dt = min(now - self._last_t, self.idle_reset) if self._last_t else self.idle_reset
            self._last_t = now
            self.x = min(max(self.x + self._step(dx, dt), 0.0), self.width - 1)
            self.y = min(max(self.y + self._step(dy, dt), 0.0), self.height - 1)
            self.history.append((now, self.x, self.y))
        self._rel_buttons = buttons
        self._buttons(buttons | self._abs_buttons, now)
        if wheel:
            self.emit("scroll", amount=wheel, x=round(self.x, 1), y=round(self.y, 1))

    def reset_buttons(self) -> None:
        """The device went away (power cycle, reset): the phone drops every held button."""
        self.buttons = self._rel_buttons = self._abs_buttons = 0
        self._down_at.clear()

    def current(self) -> tuple[float, float]:
        """Position now (an absolute move glides, so x/y alone may lag behind)."""
        self._settle_glide(self.clock())
        return self.x, self.y

    def position_at(self, t: float) -> tuple[float, float]:
        """Where the pointer was at time t (for simulating capture latency)."""
        for ht, hx, hy in reversed(self.history):
            if ht <= t:
                return hx, hy
        return (self.history[0][1], self.history[0][2]) if self.history else (self.x, self.y)

    def _buttons(self, buttons: int, now: float) -> None:
        pos = (round(self.x, 1), round(self.y, 1))
        for bit, name in _BUTTON_NAMES.items():
            was, down = self.buttons & bit, buttons & bit
            if down and not was:
                self._down_at[bit] = (pos, now)
                self.emit("button_down", button=name, x=pos[0], y=pos[1])
            elif was and not down:
                start, t0 = self._down_at.pop(bit, (pos, now))
                if abs(start[0] - pos[0]) + abs(start[1] - pos[1]) < 5:
                    self.emit("click", button=name, x=pos[0], y=pos[1], held=round(now - t0, 3))
                else:
                    self.emit("drag", button=name, x1=start[0], y1=start[1], x2=pos[0], y2=pos[1], held=round(now - t0, 3))
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
                self.emit("char", ch=ch)
            elif mods & keymap.SHORTCUT_MODS:
                name = keymap.combo_name(mods, usage)
                self.shortcuts.append(name)
                self.emit("shortcut", combo=name)
            elif usage == keymap.KEYS["capslock"]:
                self.leds ^= 0x02
            else:
                if usage == keymap.KEYS["backspace"]:
                    self.text = self.text[:-1]
                self.emit("key", name=keymap.combo_name(mods, usage))
        self.pressed = set(down)


class FakeChip:
    """Byte-level CH9329 emulator. Feed it host bytes with receive(), get the chip's reply bytes."""

    def __init__(
        self,
        config: ChipConfig | None = None,
        *,
        usb_connected: bool = True,
        version: int | None = None,
        pointer: SimPointer | None = None,
        bridge: bool = False,
    ):
        self.stored = config or ChipConfig.factory_default()  # flash contents
        self.active = self.stored  # what the chip booted with
        self.usb_connected = usb_connected
        # bridge: the ESP32 firmware (GET_INFO 0x40 + output/collections/features, on-chip runs)
        self.bridge = bridge
        self.version = version if version is not None else (p.BRIDGE_VERSION if bridge else 0x30)
        self.clock: Callable[[], float] = time.monotonic  # timing of on-chip runs
        self.sleep: Callable[[float], None] = time.sleep
        # Bridge only: the phone takes reports at link events this far apart (BLE connection
        # interval, USB polling); a report waits for the next event. 0 = delivered at once.
        self.link_period = 0.0
        self.bridge_output = 0x7F  # GET_INFO byte 3: 0x01 Bluetooth, 0x02 USB, 0x7F simulator
        self.usb_strings = {0: "", 1: "", 2: ""}
        self.events: list[dict] = []  # most recent events; `seq` numbers them across trims
        self.event_seq = 0
        self.listeners: list[Callable[[dict], None]] = []
        self.pointer = pointer or SimPointer()
        self.pointer.emit = self._emit
        self.keyboard = SimKeyboard(emit=self._emit)
        # fault injection
        self.powered = True
        self.noise_on_mismatch = False
        self.drop_next = 0  # silently ignore the next N commands addressed to us
        self.fail_next: list[int] = []  # answer the next commands with these error statuses
        self.mute_next = 0  # execute the next N commands but never answer them
        self.reply_delays: list[float] = []  # hold back the next replies by these many seconds
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
            self.pointer.reset_buttons()
            self._emit("power_cycle", baud=self.baud, address=self.address)

    def _emit(self, event: str, **fields) -> None:
        self.event_seq += 1
        record = {"seq": self.event_seq, "t": time.monotonic(), "event": event, **fields}
        self.events.append(record)
        if len(self.events) > 10_000:  # a long-running simulator must not grow without bound
            del self.events[:5_000]
        for listener in self.listeners:
            listener(record)

    def events_of(self, event: str) -> list[dict]:
        return [e for e in self.events if e["event"] == event]

    def receive(self, data: bytes, at: float | None = None) -> bytes:
        """`at`: when these bytes finished arriving (defaults to now). Reports in them reach the
        simulated phone at that time, whatever the delay before this thread ran."""
        with self._lock:
            if not self.bridge:  # (the bridge times its own reports)
                self.pointer.at = at
            try:
                return self._receive(data)
            finally:
                self.pointer.at = None

    def _receive(self, data: bytes) -> bytes:
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
        if self.mute_next:
            self.mute_next -= 1
            self._emit("reply_muted", cmd=cmd)
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
            if self.bridge:
                collections = {0: 0x1F, 1: 0x0D, 2: 0x12}.get(self.work_mode, 0)
                period = round(self.link_period * 4000) if self.usb_connected else 0
                extra = (self.bridge_output, collections, p.FEATURE_REL_RUN, period & 0xFF, period >> 8)
            else:
                extra = (0, 0, 0, 0, 0)
            return S.OK, bytes([self.version, int(self.usb_connected), self.keyboard.leds, *extra])
        if cmd == p.CMD_MS_REL_RUN and self.bridge:
            return self._rel_run(d), None
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
            self.pointer.reset_buttons()
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

    def _link_event(self) -> None:
        """Bridge: hold a report until the phone's next link event takes it."""
        if self.bridge and self.link_period > 0:
            now = self.clock()
            events = math.ceil(now / self.link_period - 1e-9)
            self.sleep(max(0.0, events * self.link_period - now))

    def take_reply_delay(self) -> float:
        return self.reply_delays.pop(0) if self.reply_delays else 0.0

    def _rel_run(self, d: bytes) -> int:
        """Bridge vendor command: `count` relative reports `interval` ms apart on the chip's clock;
        the run owns `count` slots, so a run queued behind it keeps the same schedule."""
        S = p.Status
        if len(d) != 5:
            return S.BAD_PARAM
        dx, dy = (int.from_bytes(d[i : i + 1], "little", signed=True) for i in (0, 1))
        count, interval, buttons = d[2], d[3], d[4]
        if count == 0 or buttons > 0x07 or (count - 1) * interval > p.REL_RUN_MAX_MS:
            return S.BAD_PARAM
        if self.work_mode not in (0, 2) or not self.usb_connected:
            return S.EXEC_ERROR
        t0 = self.clock()
        for i in range(count):
            if i:
                self.sleep(max(0.0, t0 + i * interval / 1000 - self.clock()))
            self._link_event()
            self.pointer.relative(max(dx, -127), max(dy, -127), buttons, 0)
        self.sleep(max(0.0, t0 + count * interval / 1000 - self.clock()))
        return S.OK

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
            self._link_event()
            self.pointer.relative(dx, dy, d[1], wheel)
        else:
            x, y = int.from_bytes(d[2:4], "little"), int.from_bytes(d[4:6], "little")
            if self.pointer.absolute:
                self.pointer.absolute_report(x, y, d[1], int.from_bytes(d[6:7], "little", signed=True))
            else:  # the conservative assumption: iOS follows relative pointers only
                self._emit("mouse_abs_ignored", x=x, y=y, buttons=d[1])
        return S.OK


class FakeSerialDevice:
    """Runs a FakeChip behind a pty. Open `port` with pyserial like a real /dev/ttyUSB*.

    With `simulate_timing` the line behaves like a full-duplex UART at the configured baud: each
    frame reaches the chip only once its bytes would have crossed the wire (frames queue behind each
    other), and replies go out on their own wire, so a reply never delays the next command. A reader
    thread only timestamps arrivals, so the chip sees frames with the spacing the host sent them.
    """

    def __init__(self, chip: FakeChip, *, simulate_timing: bool = False, processing_s: float = 0.001):
        self.chip = chip
        self.simulate_timing = simulate_timing
        self.processing_s = processing_s
        self._master, self._slave = os.openpty()
        tty.setraw(self._slave)
        self.port = os.ttyname(self._slave)
        self._stop = threading.Event()
        self._rx: deque = deque()
        self._rx_cond = threading.Condition()
        self._tx: deque = deque()
        self._tx_cond = threading.Condition()
        self._split = bytearray()
        self._rx_free_at = 0.0
        self._tx_free_at = 0.0
        self._threads = [
            threading.Thread(target=target, name=f"fake-ch9329-{name}", daemon=True)
            for name, target in (("rx", self._read_loop), ("chip", self._chip_loop), ("tx", self._write_loop))
        ]
        for t in self._threads:
            t.start()

    def line_baud(self) -> int | None:
        """Baud rate the host set on the pty."""
        return _SPEEDS.get(termios.tcgetattr(self._slave)[5])

    def _read_loop(self) -> None:
        while not self._stop.is_set():
            ready, _, _ = select.select([self._master], [], [], 0.05)
            if not ready:
                continue
            try:
                data = os.read(self._master, 4096)
            except OSError:
                return
            with self._rx_cond:
                self._rx.append((time.monotonic(), data))
                self._rx_cond.notify()

    def _frames(self, data: bytes) -> list[bytes]:
        """Split a chunk at frame boundaries (so each frame gets its own arrival time); anything
        that is not a complete frame passes through as it is."""
        self._split += data
        out = []
        while True:
            i = self._split.find(p.HEADER)
            if i != 0:
                if i < 0:
                    i = len(self._split)
                if i:
                    out.append(bytes(self._split[:i]))
                    del self._split[:i]
                if not self._split:
                    return out
            if len(self._split) < 5 or len(self._split) < 6 + self._split[4]:
                if len(self._split) >= 5 and self._split[4] > p.MAX_DATA_LEN:
                    out.append(bytes(self._split[:2]))
                    del self._split[:2]
                    continue
                return out
            n = 6 + self._split[4]
            out.append(bytes(self._split[:n]))
            del self._split[:n]

    def _chip_loop(self) -> None:
        while not self._stop.is_set():
            with self._rx_cond:
                if not self._rx:
                    self._rx_cond.wait(0.05)
                    continue
                arrived, data = self._rx.popleft()
            if not self.chip.powered:
                continue
            baud = self.line_baud()
            if baud != self.chip.baud:
                self.chip._emit("baud_mismatch", host=baud, chip=self.chip.baud, bytes=len(data))
                if self.chip.noise_on_mismatch:
                    self._write(b"\xf0\x0f\xfe")
                continue
            if not self.simulate_timing:
                out = self.chip.receive(data, at=arrived)
                if out:
                    delay = self.chip.take_reply_delay()
                    with self._tx_cond:
                        if delay or self._tx:  # replies leave in order, behind a delayed one
                            self._tx.append((time.monotonic() + delay, out))
                            self._tx_cond.notify()
                            continue
                    self._write(out)
                continue
            for part in self._frames(data):
                self._rx_free_at = max(arrived, self._rx_free_at) + len(part) * 10 / baud
                delay = self._rx_free_at - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                out = self.chip.receive(part, at=self._rx_free_at)
                if out:
                    start = max(time.monotonic() + self.processing_s + self.chip.take_reply_delay(), self._tx_free_at)
                    self._tx_free_at = start + len(out) * 10 / baud
                    with self._tx_cond:
                        self._tx.append((self._tx_free_at, out))
                        self._tx_cond.notify()

    def _write_loop(self) -> None:
        while not self._stop.is_set():
            with self._tx_cond:
                if not self._tx:
                    self._tx_cond.wait(0.05)
                    continue
                at, out = self._tx[0]
            delay = at - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            with self._tx_cond:
                self._tx.popleft()
            self._write(out)

    def _write(self, data: bytes) -> None:
        try:
            os.write(self._master, data)
        except OSError:
            pass

    def close(self) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=1)
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
