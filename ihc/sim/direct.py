"""A deterministic simulated phone: FakeChip driven in-process on a virtual clock.

The pty rig (ihc.sim.rig) runs the real serial driver but inherits the host's thread scheduling,
so timing-sensitive results (pointer acceleration) jitter under load. This one calls the chip
directly and advances a virtual clock on every sleep, so pointer planning and calibration can be
tested exactly and fast.
"""

from __future__ import annotations

from collections import Counter
from typing import Callable, Sequence

from ..hid import protocol as p
from ..hid.base import HidStatusError, HidTimeout
from ..hid.fake import FakeChip
from ..models import get_model
from .phone import SimPhone


class VirtualClock:
    def __init__(self, t: float = 1000.0):
        self.t = t
        self.tickers: list[Callable[[], None]] = []  # called after every sleep: what happens with time

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        if s > 0:
            self.t += s
            for tick in self.tickers:
                tick()


class DirectHid:
    """HidBackend over a FakeChip without serial: every command is executed and acknowledged
    synchronously, and costs `wire_s` of virtual time."""

    wait_ack = True

    def __init__(self, chip: FakeChip, clock: VirtualClock, wire_s: float = 0.0):
        self.chip = chip
        self.clock = clock
        self.wire_s = wire_s
        self.port = "direct"
        self.baud = 0
        self.stats: Counter[str] = Counter()
        self.async_errors: list = []
        self.drop_next = 0  # make the next N commands time out (after executing them, the worst case)

    def _cmd(self, frame: bytes) -> p.Frame:
        out = self.chip.receive(frame)
        self.clock.sleep(self.wire_s)
        self.stats["tx"] += 1
        if self.drop_next:
            self.drop_next -= 1
            raise HidTimeout("simulated lost reply")
        (reply,) = p.FrameParser().feed(out)
        if reply.is_error or (reply.status not in (None, 0)):
            raise HidStatusError(f"status {reply.status:#x}", frame[3], reply.status)
        return reply

    def info(self) -> dict:
        d = self._cmd(p.get_info()).data
        return {"version": "V1.0", "usb_connected": d[1] == 1, "caps_lock": bool(d[2] & 2)}

    def keyboard(self, modifiers: int, keys: Sequence[int]) -> None:
        self._cmd(p.kb_general(modifiers, keys))

    def media(self, code: int) -> None:
        self._cmd(p.kb_media(code))

    def mouse_rel(self, dx: int, dy: int, buttons: int = 0, wheel: int = 0) -> None:
        self._cmd(p.mouse_rel(dx, dy, buttons, wheel))

    def mouse_abs(self, x: int, y: int, buttons: int = 0, wheel: int = 0) -> None:
        self._cmd(p.mouse_abs(x, y, buttons, wheel))

    def supports_rel_run(self) -> bool:
        return self.chip.bridge

    def report_period(self) -> float | None:
        return self.chip.link_period if self.chip.bridge and self.chip.link_period > 0 else None

    def bridge_feature(self, name: str) -> bool:
        return self.chip.bridge and name in ("rel_run", "rel_run_quarter_ms", "rel_run_late")

    def mouse_rel_runs(self, runs, interval_ms: float, buttons: int = 0) -> None:
        quarter = abs(interval_ms - round(interval_ms)) > 1e-9
        for dx, dy, count in runs:
            self._cmd(p.mouse_rel_run(dx, dy, count, interval_ms, buttons, quarter=quarter))

    def release_all(self) -> None:
        self.keyboard(0, [])
        self.mouse_rel(0, 0)

    def sync(self) -> None:
        pass

    def close(self) -> None:
        pass


def direct_phone(model: str = "iphone-15", *, bridge: bool = False, **phone_options) -> tuple[SimPhone, FakeChip, DirectHid, VirtualClock]:
    clock = VirtualClock()
    chip = FakeChip(bridge=bridge)
    chip.clock, chip.sleep = clock, clock.sleep
    chip.pointer.clock = clock
    chip.pointer.history.clear()
    phone = SimPhone(get_model(model), clock=clock, **phone_options)
    phone.attach(chip)
    clock.tickers.append(phone.tick)
    # each command costs the wire time of an 11-byte frame at 9600 baud
    return phone, chip, DirectHid(chip, clock, wire_s=11 * 10 / 9600), clock
