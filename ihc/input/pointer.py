"""Open-loop pointer control for the iOS AssistiveTouch pointer, without looking at the screen.

iOS follows relative mouse reports only and accelerates them, so every positioned action is built as:
1. anchor: slam the pointer into the screen corner nearest to the target with (+-127, +-127)
   reports. The pointer stops at the screen edges, so its position is then known exactly;
2. per axis (X, then Y): a run of `step`-unit reports at a fixed interval, a rest, a run of
   `fine_step`-unit reports, a rest. Each run starts from rest and keeps a constant pace, so the
   distance it covers depends only on its length; the calibration records that distance, per
   direction, as d(n) = a * n + b (b absorbs the start-up of iOS acceleration and edge effects);
3. the action (click, press, wheel...).
Re-anchoring before every action keeps errors from adding up: each action carries only the
calibration error of at most half a screen of travel.

If the phone follows absolute pointer reports (to be verified per phone: the calibration detects it),
`mode` is "absolute": a move is one report placing the pointer directly (0..4095 grid mapped to the
screen by `abs_map`), then a short wait while iOS glides the cursor there. No anchoring, no drift.

Reliability: every report is acknowledged by the chip (driver in ack mode). Reports that only carry
state (buttons, keys, zero movement) are resent after an ambiguous failure; a movement report is
not, since it may have been applied: the whole move is redone from a fresh anchor instead.
"""

from __future__ import annotations

import json
import math
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from ..hid import protocol as p
from ..hid.base import HidBackend, HidStatusError, HidTimeout

# Statuses meaning the chip rejected a damaged frame without executing it: safe to resend anything.
NOT_EXECUTED = frozenset({p.Status.TIMEOUT, p.Status.BAD_HEADER, p.Status.BAD_CHECKSUM})


class PointerError(RuntimeError):
    pass


class PointerDesync(PointerError):
    """A movement report may or may not have been applied: the position is unknown."""


@dataclass
class RunModel:
    """Points covered by a run of n same-size reports that starts from rest: a * n + b for n >= 1."""

    a: float
    b: float = 0.0

    def distance(self, n: int) -> float:
        return 0.0 if n <= 0 else self.a * n + self.b


@dataclass
class DirectionModel:
    """Travel in one direction of one axis, e.g. rightwards from the left edge."""

    coarse: RunModel
    fine: RunModel

    def plan(self, d: float, max_coarse: int = 400, slack: float = 0.3) -> tuple[int, int, float]:
        """(coarse reports, fine reports, predicted distance) covering distance d >= 0: the fewest
        reports among the plans within `slack` points of the most accurate one. Fine runs are
        capped at about one coarse report's worth, so accuracy never costs hundreds of reports."""
        if d <= 0:
            return 0, 0, 0.0
        c, f = self.coarse, self.fine
        max_fine = math.ceil((c.a + abs(c.b) + abs(f.b)) / f.a) + 2 if f.a > 0 else 0
        est = int((d - c.b) / c.a) if c.a > 0 and d > c.b else 0
        plans = []
        for nc in range(max(0, est - 1), min(max_coarse, est + 1) + 1):
            left = d - c.distance(nc)
            nf0 = max(0, round((left - f.b) / f.a)) if f.a > 0 and left > 0 else 0
            for nf in {0, max(0, nf0 - 1), nf0, nf0 + 1}:
                if nf <= max_fine:
                    got = c.distance(nc) + f.distance(nf)
                    plans.append((abs(got - d), nc + nf, nc, nf, got))
        best = min(pl[0] for pl in plans)
        pick = min((pl for pl in plans if pl[0] <= best + slack), key=lambda pl: (pl[1], pl[0]))
        return pick[2], pick[3], pick[4]


def _guess_direction() -> DirectionModel:
    # Placeholder until calibrated: roughly a mid Tracking Speed at the default pace.
    return DirectionModel(RunModel(20.0, 0.0), RunModel(1.2, 0.0))


@dataclass
class PointerCalibration:
    """Per-phone model. Valid only for the iOS Tracking Speed it was measured with."""

    screen_pt: tuple[float, float] = (393.0, 852.0)  # portrait screen size in points
    step: int = 24  # HID units per coarse report
    fine_step: int = 3  # HID units per fine report
    interval: float = 0.02  # seconds between reports inside a run (start to start)
    rest: float = 0.1  # minimum idle time before a run, so every run starts from rest
    reset_reports: int = 12  # (+-127, +-127) reports that always reach a corner
    # where an anchored pointer really sits, in points inside each edge (left, top, right, bottom)
    edges: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    right: DirectionModel = field(default_factory=_guess_direction)
    left: DirectionModel = field(default_factory=_guess_direction)
    down: DirectionModel = field(default_factory=_guess_direction)
    up: DirectionModel = field(default_factory=_guess_direction)
    mode: str = "relative"  # or "absolute" when the phone follows absolute reports
    # absolute grid value = a * points + b, per axis: (ax, bx, ay, by); None = whole screen
    abs_map: tuple[float, float, float, float] | None = None
    abs_settle: float = 0.08  # iOS glides the cursor to an absolute position; wait before clicking
    method: str = "guess"  # "guess", "safari", "sim"
    measured_at: str = ""
    notes: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def calibrated(self) -> bool:
        return self.method != "guess"

    def direction(self, axis: int, sign: int) -> DirectionModel:
        return (self.right if sign > 0 else self.left) if axis == 0 else (self.down if sign > 0 else self.up)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> PointerCalibration:
        d = dict(d)
        for key in ("right", "left", "down", "up"):
            if key in d:
                m = d[key]
                d[key] = DirectionModel(RunModel(**m["coarse"]), RunModel(**m["fine"]))
        for key in ("screen_pt", "edges", "abs_map"):
            if d.get(key) is not None:
                d[key] = tuple(d[key])
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in known})

    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(self.to_dict(), indent=2) + "\n")

    @classmethod
    def load(cls, path: str | Path) -> PointerCalibration:
        return cls.from_dict(json.loads(Path(path).read_text()))


@dataclass
class MovePlan:
    anchor: tuple[int, int]  # (-1 left | +1 right, -1 top | +1 bottom)
    runs: list[tuple[int, int, int]]  # (axis, signed coarse reports, signed fine reports)
    predicted_pt: tuple[float, float]

    def to_dict(self) -> dict:
        return {"anchor": list(self.anchor), "runs": [list(r) for r in self.runs],
                "predicted_pt": [round(v, 2) for v in self.predicted_pt]}


class PointerModel:
    """Paced, acknowledged relative moves; positions in points, (0, 0) = top-left of the screen.

    `position` is the estimate after the last planned move, None when unknown (never anchored,
    after raw relative moves, or after an ambiguous failure).
    """

    def __init__(
        self,
        hid: HidBackend,
        calibration: PointerCalibration | None = None,
        *,
        retries: int = 2,
        pipeline: bool = True,
        timing_tolerance: float = 0.006,
        attempts: int = 3,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.hid = hid
        self.cal = calibration or PointerCalibration()
        self.retries = retries
        self.pipeline = pipeline
        self.timing_tolerance = timing_tolerance
        self.attempts = attempts
        self.timing_retries = 0
        self.position: tuple[float, float] | None = None
        self.buttons = 0
        self.reports = 0
        self.resends = 0
        self.last_sent = 0.0  # clock() when the latest report started going out
        self.last_motion = 0.0  # same, for the latest report that moved the pointer
        self._clock = clock
        self._sleep = sleep
        self._next_at = 0.0
        self._rest_until = 0.0
        self._abs: tuple[int, int] | None = None  # last absolute grid position sent

    # -- reports -------------------------------------------------------------------------------

    def send(self, dx: int = 0, dy: int = 0, wheel: int = 0, *, paced: bool = True) -> None:
        """One acknowledged report carrying the current buttons, paced by the calibration interval."""
        if paced:
            # movement after a rest() waits out the rest; buttons and wheel only keep the pace
            due = max(self._next_at, self._rest_until) if (dx or dy) else self._next_at
            now = self._clock()
            if now < due:
                self._sleep(due - now)
        # fixed-rate pacing: the interval runs from the start of one report to the start of the
        # next, so the time spent waiting for the ack does not stretch it (speed = distance to iOS)
        start = self._clock()
        idempotent = dx == 0 and dy == 0 and wheel == 0
        for attempt in range(self.retries + 1):
            try:
                self.hid.mouse_rel(dx, dy, self.buttons, wheel)
                break
            except HidStatusError as e:
                if e.status in NOT_EXECUTED and attempt < self.retries:
                    self.resends += 1
                    continue
                if not idempotent:
                    self.position = None
                raise
            except HidTimeout as e:
                if idempotent and attempt < self.retries:
                    self.resends += 1
                    continue
                self.position = None
                raise PointerDesync(f"movement report may have been lost or applied: {e}") from e
        self.reports += 1
        self.last_sent = start
        if dx or dy:
            self.last_motion = start
            self._abs = None  # a relative move leaves the last absolute position
        self._next_at = start + self.cal.interval

    def rest(self) -> None:
        """The next movement report waits until the pointer has been idle for `cal.rest`."""
        self._rest_until = self.last_motion + self.cal.rest

    # -- absolute reports -----------------------------------------------------------------------

    def abs_grid(self, x: float, y: float) -> tuple[int, int]:
        w, h = self.cal.screen_pt
        ax, bx, ay, by = self.cal.abs_map or (4095 / w, 0.0, 4095 / h, 0.0)
        return (min(4095, max(0, round(ax * x + bx))), min(4095, max(0, round(ay * y + by))))

    def send_abs(self, gx: int, gy: int, wheel: int = 0) -> None:
        """One acknowledged absolute report with the current buttons. It carries the whole pointer
        state (position + buttons), so it is simply resent after an ambiguous failure."""
        now = self._clock()
        if now < self._next_at:
            self._sleep(self._next_at - now)
        start = self._clock()
        for attempt in range(self.retries + 1):
            try:
                self.hid.mouse_abs(gx, gy, self.buttons, wheel)
                break
            except (HidTimeout, HidStatusError) as e:
                retryable = isinstance(e, HidTimeout) or e.status in NOT_EXECUTED
                if not retryable or attempt == self.retries or wheel:
                    self.position = None
                    raise
                self.resends += 1
        self.reports += 1
        self.last_sent = self.last_motion = start
        self._next_at = start + self.cal.interval
        self._abs = (gx, gy)

    def _buttons_changed(self) -> None:
        if self.cal.mode == "absolute" and self._abs is not None:
            self.send_abs(*self._abs)
        else:
            self.send()

    # -- planned moves -------------------------------------------------------------------------

    def anchor(self, sx: int, sy: int) -> None:
        """Pin the pointer into a corner: sx -1 left / +1 right, sy -1 top / +1 bottom."""
        if self.buttons:
            raise PointerError("refusing to anchor while a button is held: it would drag")
        # No pacing needed (they only have to reach the corner): as fast as the line allows, with
        # every ack still checked at the end of the burst.
        with self._pipelined():
            for _ in range(self.cal.reset_reports):
                self.send(127 * sx, 127 * sy, paced=False)
        self.rest()
        self.position = self.anchored_at(sx, sy)

    def anchored_at(self, sx: int, sy: int) -> tuple[float, float]:
        w, h = self.cal.screen_pt
        el, et, er, eb = self.cal.edges
        return (el if sx < 0 else w - er, et if sy < 0 else h - eb)

    def run(self, axis: int, coarse: int, fine: int) -> None:
        """A coarse run then a fine run along one axis (signed report counts), each from rest.

        The distance a run covers depends on its pace (iOS acceleration is speed-based), so the
        send times are checked: a report that went out late (host stalled) makes the position
        unknown, and the caller redoes the move."""
        for n, units in ((coarse, self.cal.step), (fine, self.cal.fine_step)):
            if not n:
                continue
            u = units if n > 0 else -units
            starts = [self.last_motion]
            with self._pipelined():
                for _ in range(abs(n)):
                    if axis == 0:
                        self.send(u, 0)
                    else:
                        self.send(0, u)
                    starts.append(self.last_sent)
            self._check_pace(starts)
            self.rest()

    def _check_pace(self, starts: list[float]) -> None:
        tol = self.timing_tolerance
        gaps = [b - a for a, b in zip(starts, starts[1:])]
        short_rest = bool(gaps) and starts[0] > 0 and gaps[0] < self.cal.rest - 0.002
        bad = [g for g in gaps[1:] if not (self.cal.interval - 0.002 <= g <= self.cal.interval + tol)]
        if short_rest or bad:
            self.position = None
            self.timing_retries += 1
            worst = max((abs(g - self.cal.interval) for g in gaps[1:]), default=0.0)
            raise PointerDesync(f"report pacing off by up to {worst * 1000:.1f} ms (host busy?)")

    @contextmanager
    def _pipelined(self):
        """Inside a run, reports go out without waiting for each ack (only the serial line limits
        the pace), but every ack is still collected at the end: a missing or failed one means the
        position is unknown, and the caller redoes the move from a fresh anchor."""
        hid = self.hid
        if not self.pipeline or not hasattr(hid, "sync") or not getattr(hid, "wait_ack", False):
            yield
            return
        lost0, err0 = hid.stats["lost_acks"], len(hid.async_errors)
        hid.wait_ack = False
        try:
            yield
        finally:
            hid.wait_ack = True
            hid.sync()
        failed = hid.async_errors[err0:]
        if hid.stats["lost_acks"] != lost0 or failed:
            self.position = None
            real = [(cmd, st) for cmd, st in failed if st not in NOT_EXECUTED]
            if real:  # the chip refused (e.g. phone locked): retrying cannot help, say why
                from ..hid.ch9329 import status_error

                raise status_error(*real[0])
            raise PointerDesync("a report inside a run was not acknowledged or failed")

    def plan(self, x: float, y: float) -> MovePlan:
        """Anchor at the nearest corner, then cover the remaining distance per axis."""
        w, h = self.cal.screen_pt
        sx = -1 if x <= w / 2 else 1
        sy = -1 if y <= h / 2 else 1
        start = self.anchored_at(sx, sy)
        runs, predicted = [], []
        for axis, target, sign in ((0, x, sx), (1, y, sy)):
            direction = -sign  # away from the anchor edge
            d = (target - start[axis]) * direction
            nc, nf, got = self.cal.direction(axis, direction).plan(d)
            runs.append((axis, direction * nc, direction * nf))
            predicted.append(start[axis] + direction * got)
        return MovePlan((sx, sy), runs, (predicted[0], predicted[1]))

    def move_to(self, x: float, y: float) -> MovePlan:
        """Anchored open-loop move to (x, y) points; redone from a fresh anchor if a report is lost."""
        w, h = self.cal.screen_pt
        if not (0 <= x <= w and 0 <= y <= h):
            raise PointerError(f"target outside the screen: ({x:.1f}, {y:.1f}) pt on {w:.0f}x{h:.0f}")
        if self.cal.mode == "absolute":
            gx, gy = self.abs_grid(x, y)
            self.send_abs(gx, gy)
            self._sleep(self.cal.abs_settle)
            self.position = (x, y)
            return MovePlan((0, 0), [], (x, y))
        plan = self.plan(x, y)
        for attempt in range(self.attempts):
            try:
                self.anchor(*plan.anchor)
                for axis, coarse, fine in plan.runs:
                    self.run(axis, coarse, fine)
                self.position = plan.predicted_pt
                return plan
            except PointerDesync:
                if attempt == self.attempts - 1:
                    raise
        return plan

    def move_to_norm(self, nx: float, ny: float) -> MovePlan:
        w, h = self.cal.screen_pt
        return self.move_to(nx * w, ny * h)

    def drag_by(self, dx: float, dy: float, duration: float = 0.3) -> None:
        """With the current buttons held, move by (dx, dy) points. Absolute mode glides there in
        reports spread over `duration`; relative mode runs X then Y from rest (no anchoring is
        possible while pressed, so it relies on the run models alone)."""
        if self.cal.mode == "absolute" and self.position is not None:
            x0, y0 = self.position
            n = max(2, round(duration / self.cal.interval))
            for i in range(1, n + 1):
                self.send_abs(*self.abs_grid(x0 + dx * i / n, y0 + dy * i / n))
            self._sleep(self.cal.abs_settle)
            self.position = (x0 + dx, y0 + dy)
            return
        for axis, d in ((0, dx), (1, dy)):
            if d:
                sign = 1 if d > 0 else -1
                nc, nf, _ = self.cal.direction(axis, sign).plan(abs(d))
                self.run(axis, sign * nc, sign * nf)
        if self.position is not None:
            self.position = (self.position[0] + dx, self.position[1] + dy)

    def raw(self, dx: int, dy: int, wheel: int = 0) -> None:
        """Unplanned relative report (live remote control): the position becomes unknown."""
        self.send(dx, dy, wheel, paced=False)
        if dx or dy:
            self.position = None

    # -- buttons -------------------------------------------------------------------------------

    def press(self, button: int = p.MOUSE_LEFT) -> None:
        self.buttons |= button
        self._buttons_changed()

    def release(self, button: int | None = None) -> None:
        self.buttons &= 0 if button is None else ~button
        self._buttons_changed()

    def click(self, button: int = p.MOUSE_LEFT, hold: float = 0.06) -> None:
        self.press(button)
        try:
            self._sleep(hold)
        finally:
            self.release(button)

    def scroll(self, amount: int) -> None:
        """Wheel detents, one per report; positive scrolls up."""
        for _ in range(abs(amount)):
            if self.cal.mode == "absolute" and self._abs is not None:
                self.send_abs(*self._abs, wheel=1 if amount > 0 else -1)
            else:
                self.send(wheel=1 if amount > 0 else -1)
