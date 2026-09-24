"""Pointer calibration through a web page opened in Safari on the phone (no computer vision).

The page (web/calibrate.html) reports the page coordinates of every click. From clicks alone:
- entry: from the top-left corner the pointer steps down until a click lands on the page; the steps
  are bounded so that they never reach Safari's bottom bar (see _enter_page);
- run models: the distance a run covers from rest is the difference between the click before it and
  the click after it, so no page offset is involved. Runs alternate in direction around the middle
  of the page, and a run goes out only when the worst case of what was measured so far still ends
  inside the page, so no click is ever planned on Safari's bars;
- edge offsets: where an anchored pointer really sits (iOS may clamp it one point inside an edge).
  Left and right come straight from the page (it spans the screen width). Vertically the page is
  shifted by the status bar and Safari's bars by an unknown offset; the top edge is taken as 0
  (the pointer's hot spot clamps at the edge; the left edge measurement checks that assumption), which
  gives the page offset, and the bottom edge follows;
- reset length: the fewest corner-slam reports that still reach the corner from the far side.
- validation: planned moves to random points (central ones first), with the landing error; a
  calibration with an error above `max_error` is refused.

Absolute pointer reports are tried first, once the pointer sits safely on the page: if clicks land
where absolute reports put them, the phone follows absolute positioning and only the grid-to-screen
map is measured (seconds instead of a minute). Otherwise the relative models above are measured.

Page events: JSON objects posted in the order they happen, one request at a time.
    every event   "type", "seq" (1, 2, 3... per page load), "t" (ms since page load: performance.now())
    hello         "screen_w", "screen_h", "inner_w", "inner_h", "dpr", "ua", "heartbeat_ms"; posted at
                  load, on resize, and again every heartbeat_ms (the heartbeat)
    click         "x", "y" (clientX/Y, points), "button"
    move          "x", "y" (hover, when Safari reports it)
A click that brings no event is ambiguous: it missed the page, or its event is late or lost. `seq`
and the heartbeats settle it: an event the page generated after the click (by `t`) arriving with
no click before it proves the click missed (events come in order), and a gap in `seq` means events
were lost, which stops the calibration. Events without seq and t (an older page) still work, with
the more cautious rules noted where they apply.
"""

from __future__ import annotations

import math
import random
import statistics
import threading
import time
from collections import deque
from dataclasses import replace

from .input.pointer import DirectionModel, PointerCalibration, PointerDesync, PointerModel, RunModel


# Travel per coarse report: fast, while a fine run still covers one of them. Lower it (e.g. 10) when
# the validation error shows timing jitter matters (acceleration grows with speed, so slower coarse
# reports are less sensitive to it).
COARSE_TARGET_PT = 20.0
FINE_TARGET_PT = 1.0  # travel per fine report: the resolution of a tap
MAX_ERROR_PT = 3.0  # validation: the largest landing error an accepted calibration may show

# Looking for the page: reports of the default coarse size, one per run (each from rest, so each
# covers the same distance whatever the acceleration).
ENTRY_STEP = 24
ENTRY_MAX_PT = 60.0  # the most one such report is assumed to cover, when the page cannot confirm misses
ENTRY_MAX_REPORTS = 400
TOP_MAX = 0.2  # the page top is at most this fraction of the screen height down (see _enter_page)
ENTRY_FLOOR = 0.85  # entry clicks stay above this fraction of the page height from the screen top
EDGE_MARGIN = 0.06  # runs are planned to stop at least this fraction of the page inside it
SAFETY = 1.25  # on the worst case predicted for a run: timing jitter, a model not quite linear
CONFIRM_MARGIN_S = 0.3  # an event the page stamped this long after a click surely came after it
POLL_S = 0.01
ENTRY_TOP_TOLERANCE_PT = 12.0  # the page offset from the way onto the page is rough (a few points)


class CalibrationError(RuntimeError):
    pass


class ClickCollector:
    """Events posted by the calibration page (over HTTP, or directly by the simulator), in order."""

    def __init__(self, maxlen: int = 10_000) -> None:
        # bounded: an open page keeps sending heartbeats whether or not anyone calibrates
        self._events: deque[dict] = deque(maxlen=maxlen)
        self._cond = threading.Condition()
        self._hello = threading.Event()
        self.page: dict | None = None
        self.moves = 0  # pointermove events seen: tells whether Safari reports hover positions

    def push(self, event: dict) -> None:
        kind = event.get("type")
        with self._cond:
            if kind == "hello":
                self.page = event
            elif kind == "move":
                self.moves += 1
            self._events.append(event)
            self._cond.notify_all()
        if kind == "hello":
            self._hello.set()

    def clear(self) -> None:
        with self._cond:
            self._events.clear()

    def take(self) -> list[dict]:
        """Every event received since the last take (or clear), oldest first."""
        with self._cond:
            out = list(self._events)
            self._events.clear()
        return out

    def wait_page(self, timeout: float) -> dict:
        if not self._hello.wait(timeout):
            raise CalibrationError("the calibration page did not load on the phone")
        return self.page

    def next_click(self, timeout: float) -> dict | None:
        """The next click event, dropping the other events before it."""
        deadline = time.monotonic() + timeout
        with self._cond:
            while True:
                while self._events:
                    ev = self._events.popleft()
                    if ev.get("type") == "click":
                        return ev
                left = deadline - time.monotonic()
                if left <= 0:
                    return None
                self._cond.wait(left)


def _line(pairs: list[tuple[float, float]]) -> tuple[float, float]:
    """Least squares y = a * x + b over (x, y) pairs with at least two distinct x."""
    xs, ys = [x for x, _ in pairs], [y for _, y in pairs]
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    a = sum((x - mx) * (y - my) for x, y in pairs) / sum((x - mx) ** 2 for x in xs)
    return a, my - a * mx


def _fit(samples: list[tuple[int, float]]) -> RunModel:
    """Least squares d = a * n + b."""
    if len({n for n, _ in samples}) < 2:
        raise CalibrationError("need runs of at least two lengths to fit a model")
    a, b = _line(samples)
    return RunModel(round(a, 4), round(b, 4))


def _retry(fn, attempts: int = 4):
    """Run an anchored probe again when its pacing was off (the anchor makes it repeatable)."""
    for i in range(attempts):
        try:
            return fn()
        except PointerDesync:
            if i == attempts - 1:
                raise


def _scale(k: float) -> float:
    """How much farther a report k times the size may go: at most k times when smaller, k^2 when
    larger (acceleration grows at most in proportion to speed)."""
    return k if k <= 1 else k * k


class _Travel:
    """What runs of the current report size cover from rest along one axis (both directions): a
    central estimate, and a worst case for runs that must stay on the page."""

    def __init__(self, ratio: float) -> None:
        self.ratio = ratio  # a report inside a run covers at most this many times the first one
        self.runs: list[tuple[int, float]] = []  # (reports, points) measured at this size
        self.bound: list[tuple[int, float]] = []  # the same carried over from another size: bounds only

    @property
    def longest(self) -> int:
        return max((n for n, _ in self.runs), default=0)

    def add(self, n: int, d: float) -> None:
        self.runs.append((n, d))

    def resize(self, k: float) -> None:
        """The report size was multiplied by k: what was measured only bounds the new size."""
        f = _scale(k)
        self.bound = [(n, d * f) for n, d in self.runs + self.bound]
        self.runs = []

    def predict(self, n: int) -> float | None:
        if len({k for k, _ in self.runs}) >= 2:
            a, b = _line(self.runs)
            return a * n + b
        known = self.runs or self.bound
        return max(d / k for k, d in known) * n if known else None

    def upper(self, n: int) -> float:
        """The farthest a run of n reports may go (inf when nothing is known). A run from rest at a
        steady pace covers d(n) = first + (n - 1) * next, with next at most `ratio` * first: so a
        measured d(k) gives d(n) <= d(k) for n <= k, and d(k) * (n - 1) / (k - 1) beyond."""
        best = math.inf
        for k, d in self.runs + self.bound:
            if n <= k:
                u = d
            elif k >= 2:
                u = d * (n - 1) / (k - 1)
            else:
                u = d * (1 + self.ratio * (n - 1))
            best = min(best, u)
        return SAFETY * best

    def pick(self, want: float, room: float) -> int:
        """Run length towards a point `want` away: the shortest that is predicted to get there,
        among those that surely stop within `room` and are at most twice the longest run measured
        (runs grow 1, 2, 4...). 0 when even one report might not stop within `room`."""
        best = 0
        for n in range(1, max(1, 2 * self.longest) + 1):
            if self.upper(n) > room:
                break
            best = n
            if (self.predict(n) or 0.0) >= want:
                break
        return best


class _Rates:
    """_Travel per axis, for coarse and for fine reports."""

    def __init__(self, ratio: float) -> None:
        self._t = {(axis, fine): _Travel(ratio) for axis in (0, 1) for fine in (False, True)}

    def __call__(self, axis: int, fine: bool = False) -> _Travel:
        return self._t[(axis, fine)]

    def resize(self, fine: bool, old: int, new: int) -> None:
        for axis in (0, 1):
            self._t[(axis, fine)].resize(new / old)

    def seed_fine(self, step: int, fine_step: int) -> None:
        """Bounds for fine runs from the coarse ones, until fine runs are measured."""
        f = _scale(fine_step / step)
        for axis in (0, 1):
            coarse, fine = self._t[(axis, False)], self._t[(axis, True)]
            if not fine.runs:
                fine.bound = [(n, d * f) for n, d in coarse.runs + coarse.bound]


class _Session:
    """Clicks and the page events that answer them.

    Every click waits for its answer: its click event, a proof that it missed (an event the page
    generated after the click, arriving first), or nothing within the timeout. Only where a miss is
    expected (see `click`) is a miss accepted; anything else stops the calibration, and since events
    are matched in order and no click goes out while one is unanswered, a late event is never taken
    for a later click's."""

    def __init__(self, pm: PointerModel, clicks: ClickCollector, page: dict, timeout: float, log):
        self.pm = pm
        self.clicks = clicks
        self.timeout = timeout
        self.log = log or (lambda event, **fields: None)
        self.count = 0
        self.size = (float(page["inner_w"]), float(page["inner_h"]))
        # a page that numbers, times and heartbeats its events can prove a miss; an older one cannot
        beat = page.get("heartbeat_ms")
        self.heartbeat = float(beat) / 1000 if beat and "seq" in page and "t" in page else 0.0
        self.hover = clicks.moves > 0
        self.seq: int | None = None  # of the latest event read
        self.offset = math.inf  # host clock minus page clock, at most (s): arrival time - page time
        self.fresh = False  # offset measured on an event read right after it arrived
        self.unsure = False  # a click without event was taken as a miss: its event may still come
        self.confirmed = False  # the page proved the latest miss
        self.entry_top: float | None = None  # page offset as the way onto the page suggests it
        # how long after a click its answer may come: with heartbeats, a proof of a miss takes up to
        # CONFIRM_MARGIN_S and a heartbeat period, then `timeout` for a late network
        self.patience = timeout + (CONFIRM_MARGIN_S + 2 * self.heartbeat if self.heartbeat else 0.0)
        self._inbox: deque[dict] = deque()
        self._pulled = -math.inf

    def _pull(self) -> None:
        now = self.pm._clock()
        fresh = now - self._pulled <= 3 * POLL_S
        self._pulled = now
        for ev in self.clicks.take():
            seq = ev.get("seq")
            if isinstance(seq, (int, float)) and not isinstance(seq, bool):
                seq = int(seq)
                if self.seq is not None and seq <= self.seq:
                    continue  # sent twice (a retried request) or from a reloaded page: never a new answer
                if self.seq is not None and seq > self.seq + 1:
                    raise CalibrationError(f"events {self.seq + 1}..{seq - 1} of the calibration page were lost "
                                           "(network?); calibration cannot tell which clicks reached it")
                self.seq = seq
            t = ev.get("t")
            if isinstance(t, (int, float)) and not isinstance(t, bool):
                self.offset = min(self.offset, now - t / 1000)
                self.fresh |= fresh
            kind = ev.get("type")
            if kind == "hello" and "inner_w" in ev and "inner_h" in ev:
                w, h = float(ev["inner_w"]), float(ev["inner_h"])
                if abs(w - self.size[0]) > 1 or abs(h - self.size[1]) > 1:
                    raise CalibrationError(f"the calibration page changed size ({self.size[0]:.0f}x{self.size[1]:.0f} "
                                           f"to {w:.0f}x{h:.0f} pt: keyboard, Safari's bars, rotation?)")
            elif kind == "move":
                self.hover = True
            self._inbox.append(ev)

    def _before(self, ev: dict, t: float) -> bool:
        """The page surely generated `ev` before host time t."""
        return isinstance(ev.get("t"), (int, float)) and ev["t"] / 1000 < t - self.offset

    def _after(self, ev: dict, t: float) -> bool:
        """The page surely generated `ev` after host time t, provided the fastest event reached the
        host within CONFIRM_MARGIN_S (and the phone saw the click within it too)."""
        return self.fresh and isinstance(ev.get("t"), (int, float)) and ev["t"] / 1000 > t - self.offset + CONFIRM_MARGIN_S

    def skip(self) -> None:
        """Forget what the page sent so far: nothing of it answers what comes next."""
        self._pull()
        for ev in self._inbox:
            if ev.get("type") == "click":
                self.log("calibration_stray_click", x=ev.get("x"), y=ev.get("y"), seq=ev.get("seq"))
        self._inbox.clear()

    def wait(self, t0: float, t1: float, want: str = "click") -> tuple[str, dict | None]:
        """The page's answer to what the host did between host times t0 and t1: (want, event) for
        the first `want` event ("click" or "move") not generated before t0; ("none", None) when an
        event the page generated after t1 comes first (nothing else is on its way: events come in
        order); ("timeout", None) otherwise."""
        deadline = t1 + self.patience
        while True:
            self._pull()
            while self._inbox:
                ev = self._inbox.popleft()
                kind = ev.get("type")
                if kind == want and not self._before(ev, t0):
                    return kind, ev
                if kind == "click":
                    self.log("calibration_stray_click", x=ev.get("x"), y=ev.get("y"), seq=ev.get("seq"))
                if self.heartbeat and self._after(ev, t1):
                    return "none", None
            if self.pm._clock() >= deadline:
                return "timeout", None
            self.pm._sleep(POLL_S)

    def act(self, do, miss_ok: bool = False) -> tuple[float, float] | None:
        """Run `do` (which clicks) and return the page coordinates the page saw. A miss returns None
        only with `miss_ok`: a proven miss, or (page without heartbeats) no event at all, which is
        then remembered in `unsure`. Otherwise a click without event raises CalibrationError."""
        self.skip()
        t0 = self.pm._clock()
        do()
        t1 = self.pm._clock()
        self.count += 1
        kind, ev = self.wait(t0, t1)
        self.confirmed = kind == "none"
        if kind == "click":
            return float(ev["x"]), float(ev["y"])
        if miss_ok and (kind == "none" or not self.heartbeat):
            self.unsure |= kind == "timeout"
            self.log("calibration_miss", proven=kind == "none")
            return None
        if kind == "none":
            raise CalibrationError("a click missed the calibration page; is it still open, unscrolled and untouched?")
        raise CalibrationError(f"no event came from the calibration page within {self.patience:.2f} s of a click: "
                               "is the phone's network or the page stalled?")

    def click(self, miss_ok: bool = False) -> tuple[float, float] | None:
        """Click where the pointer is (see act). Misses are expected only above the page while
        looking for it, and when the phone may ignore absolute reports."""
        def do():
            self.pm.click()
            self.pm.rest()

        return self.act(do, miss_ok)


def calibrate(
    pm: PointerModel,
    clicks: ClickCollector,
    *,
    coarse_counts: tuple[int, ...] = (1, 2, 3, 4, 6, 8),
    fine_counts: tuple[int, ...] = (2, 4, 8, 12, 16),
    repeats: int = 2,
    validate: int = 8,
    try_absolute: bool = True,
    coarse_target: float = COARSE_TARGET_PT,
    page_timeout: float = 15.0,
    click_timeout: float = 2.0,
    max_error: float = MAX_ERROR_PT,
    seed: int = 0,
    log=None,
) -> PointerCalibration:
    """Measure `pm`'s phone with the calibration page already open (or opening) in Safari.
    On success the new calibration is installed in `pm` and returned. `click_timeout` is how late
    a page event may be (beyond the page's heartbeat period); `max_error` the largest validation
    landing error accepted, in points."""
    page = clicks.wait_page(page_timeout)
    W, H = float(page["screen_w"]), float(page["screen_h"])
    inner_w, inner_h = float(page["inner_w"]), float(page["inner_h"])
    if abs(inner_w - W) > 1:
        raise CalibrationError(f"the page is {inner_w:.0f} pt wide but the screen {W:.0f} pt: use portrait, no zoom")
    if not H / 2 <= inner_h <= H:
        raise CalibrationError(f"the page is {inner_h:.0f} pt tall on a {H:.0f} pt screen: close the keyboard and banners")
    old = pm.cal
    # start from the default coarse size: an old one may be far too fast for today's Tracking Speed
    pm.cal = replace(old, screen_pt=(W, H), reset_reports=max(old.reset_reports, 30), mode="relative", step=ENTRY_STEP)
    s = _Session(pm, clicks, page, click_timeout, log)
    try:
        pos, per = _enter_page(s, W, H, inner_h)
        rates = _Rates(max(2.0, pm.cal.rest / pm.cal.interval))
        rates(0).add(1, per)
        rates(1).bound.append((1, 1.5 * per))  # the same pointer: Y is taken as X, with a margin
        cal = _try_absolute(s, W, H, inner_h, max_error) if try_absolute else None
        if cal is None:
            pos = _resync(s) if s.unsure else s.click()  # the absolute test may have moved the pointer (on the page)
            cal = _measure(s, W, H, inner_h, pos, rates, coarse_counts, fine_counts, repeats, coarse_target)
        pm.cal = cal
        errors = _validate(s, cal, W, inner_h, validate, random.Random(seed), max_error)
    except Exception:
        pm.cal = old
        raise
    cal.extra.update({
        "validation_error_pt": {"mean": round(statistics.fmean(errors), 2), "max": round(max(errors), 2), "n": len(errors)}
        if errors else None,
        "clicks": s.count,
        "hover_events": s.hover,
        "user_agent": page.get("ua", ""),
    })
    s.log("calibrated", **cal.to_dict())
    return cal


def _top_max(H: float, inner_h: float) -> float:
    """How far down the page top can be. The page and the bars around it fill the screen, so it is
    at most H - inner_h; and above the page there is only the status bar (20 to 59 pt) and, with
    Safari's address bar at the top, that bar (about 56 pt): about 115 pt at most on current
    iPhones, under 18 % of the shortest portrait screen (667 pt). 20 % of the height keeps a margin."""
    return max(1.0, min(TOP_MAX * H, H - inner_h))


def _enter_page(s: _Session, W: float, H: float, inner_h: float) -> tuple[tuple[float, float], float]:
    """Anchor top-left, two single reports right, then down until a click lands on the page.
    Returns the page position and how far one report from rest went (from the two to the right).

    The page top is at most `top_max` down (_top_max) and the page is inner_h tall, so its bottom
    is at least inner_h down: the pointer must reach top_max but stay above inner_h, and it stays
    above ENTRY_FLOOR * inner_h. Every report down is a run of its own (from rest), so all cover the
    same distance, `per`; after `down` of them the pointer is down * per down. Steps double, sized
    so that the worst case for `per` still ends above the floor:
    - a page that confirms misses (heartbeats) proves a missed click was above the page, hence
      above top_max: per < top_max / down, and each step keeps within the floor for any such per;
    - with an older page a missing event proves nothing (the network may be down and the pointer on
      the page), so per is assumed to be at most ENTRY_MAX_PT and the descent stops at the floor.
    The clicks above the page (status bar, or Safari's address bar when it is at the top) are the only
    misses calibration expects. When Safari reports hover, the pointer goes down without clicking
    until the page reports it, so no click misses at all."""
    pm = s.pm
    top_max = _top_max(H, inner_h)
    floor = ENTRY_FLOOR * inner_h

    def start(down: int = 0) -> None:
        pm.anchor(-1, -1)
        pm.run(0, 1, 0)
        pm.run(0, 1, 0)
        for _ in range(down):
            pm.run(1, 1, 0)

    _retry(start)
    s.skip()  # hover events the anchor caused (crossing the page) only tell that Safari reports hover
    down, per_max = 0, ENTRY_MAX_PT
    while True:
        step = min(max(1, down), math.floor(floor / per_max) - down)
        if step < 1:
            raise CalibrationError("no click reached the calibration page before the pointer could have passed "
                                   f"{floor:.0f} pt down: is it open in Safari, in portrait, unscrolled?")
        if down + step > ENTRY_MAX_REPORTS:
            raise CalibrationError("the pointer barely moves: raise the Tracking Speed")
        t0 = pm._clock()
        try:
            for _ in range(step):
                pm.run(1, 1, 0)
        except PointerDesync:  # a report paced badly (not from rest): redo the way down from the corner
            _retry(lambda: start(down + step))
        down += step
        if s.hover and s.heartbeat:
            kind, _ = s.wait(t0, pm._clock(), want="move")
            if kind == "timeout":
                raise CalibrationError("the calibration page stopped sending events (network?)")
            if kind == "none":  # no hover report: still above the page
                per_max = top_max / down
                continue
        pos = s.click(miss_ok=True)
        s.log("calibration_entry", down=down, got=pos)
        if pos is not None:
            break
        if s.confirmed:
            per_max = top_max / down
    pos = _confirm(s, pos)
    if pos[0] >= W - 2:
        raise CalibrationError("one report moves the pointer across half the screen: lower the Tracking Speed")
    per = pos[0] / 2
    # the pointer went `down` reports from the top edge; each covers about what one did along X
    s.entry_top = down * per - pos[1]
    return pos, per


def _confirm(s: _Session, first: tuple[float, float]) -> tuple[float, float]:
    """Click again without moving: an event at another place means events arrive late (the first
    one answered an earlier click), and nothing can be trusted."""
    again = s.click()
    if math.dist(first, again) > 0.5:
        raise CalibrationError("the page reported a click away from the pointer: its events arrive late or "
                               "out of order (network?)")
    s.unsure = False
    return again


def _resync(s: _Session) -> tuple[float, float]:
    """After a click that got no event and was taken as a miss (a page without heartbeats cannot
    prove it), its event may still arrive and be taken for the next click's. Move (along X: the page
    spans the screen width) and click twice: only two events at one new place prove that the host
    and the page agree again."""
    _retry(lambda: s.pm.run(0, 1, 0))
    return _confirm(s, s.click())


def _abs_click(s: _Session, gx: int, gy: int, miss_ok: bool = False) -> tuple[float, float] | None:
    pm = s.pm

    def do():
        pm.send_abs(gx, gy)
        pm._sleep(0.25)  # generous: iOS glides the cursor to an absolute position
        pm.buttons = 1
        try:
            pm.send_abs(gx, gy)
            pm._sleep(0.06)
        finally:
            pm.buttons = 0
            pm.send_abs(gx, gy)
        pm.rest()

    return s.act(do, miss_ok)


def _try_absolute(s: _Session, W, H, inner_h, max_error: float) -> PointerCalibration | None:
    """Absolute reports to points of the page, from its middle outwards. None when the phone does
    not follow them (no click at all, or a click where the pointer already was), or when the map
    cannot be measured reliably (then the relative models are measured instead).

    The page gives page coordinates: X is the screen's, and Y the screen's minus the unknown page
    offset. So the grid-to-points map is identifiable along X, but along Y only its scale and the
    sum of its offset with the page's: grid y = ay * page y + c, c = ay * page top + by. The grid is
    taken to start at the screen top (by = 0) only when the fitted scale says it spans the screen
    height (as a digitizer range does); otherwise the page top is unknown in screen points.
    The first click goes to the middle of the band of the screen that is on the page whatever the
    page offset (see _enter_page); each next one is placed with the map fitted so far."""
    from .hid.base import HidError

    pm = s.pm
    top_max = _top_max(H, inner_h)
    ax, bx, ay = 4095 / W, 0.0, 4095 / H  # first guess: the grid spans the screen
    g = (round(ax * W / 2), round(ay * (top_max + inner_h) / 2))
    try:
        got = _abs_click(s, *g, miss_ok=True)
    except HidError as e:
        s.log("absolute_unsupported", error=str(e))
        return None
    if got is None or abs(got[0] - W / 2) > 0.1 * W:
        s.log("absolute_unsupported", target=list(g), got=got)
        return None
    c = g[1] - ay * got[1]
    samples = [(g, got)]

    def refit(i: int) -> tuple[float, float] | None:
        """grid = a * page + b along axis i, from page = k * grid + m (the grid values are distinct)."""
        k, m = _line([(q[i], p[i]) for q, p in samples])
        return (1 / k, -m / k) if k > 0.1 * (W if i == 0 else H) / 4095 else None

    for fx, fy in ((0.5, 0.3), (0.5, 0.7), (0.25, 0.5), (0.75, 0.5), (0.25, 0.3), (0.75, 0.3), (0.25, 0.7), (0.75, 0.7)):
        x, y = fx * W, fy * inner_h
        g = (min(4095, max(0, round(ax * x + bx))), min(4095, max(0, round(ay * y + c))))
        got = _abs_click(s, *g)
        samples.append((g, got))
        for i in (0, 1):
            if len({q[i] for q, _ in samples}) < 2:
                continue
            fit = refit(i)
            if fit is None:
                s.log("absolute_unusable", reason="clicks do not follow the absolute reports", axis=i)
                return None
            if i == 0:
                ax, bx = fit
            else:
                ay, c = fit
    worst = max(math.dist(((q[0] - bx) / ax, (q[1] - c) / ay), p) for q, p in samples)
    top = c / ay  # with by = 0
    scale_y = ay * H / 4095
    s.log("absolute_fit", ax=ax, bx=bx, ay=ay, c=c, worst_pt=round(worst, 2), scale_y=round(scale_y, 4),
          page_top=round(top, 2), entry_top=s.entry_top)
    if worst > max_error:
        s.log("absolute_unusable", reason=f"grid points off the fitted map by up to {worst:.1f} pt")
        return None
    if abs(scale_y - 1) > 0.03 or not -2 <= top <= top_max + 2:
        s.log("absolute_unusable", reason=f"the absolute Y range is {1 / scale_y:.3f} x the screen height: "
                                          "the page offset cannot be derived")
        return None
    # a full-height range that is shifted (part of it off the screen) looks the same from the page:
    # the way onto the page (relative reports from the top edge) tells the page offset roughly
    if s.entry_top is not None and abs(top - s.entry_top) > ENTRY_TOP_TOLERANCE_PT:
        s.log("absolute_unusable", reason=f"the absolute map puts the page {top:.0f} pt down, the relative "
                                          f"way onto it {s.entry_top:.0f} pt")
        return None
    full = abs(ax * W / 4095 - 1) < 0.03 and abs(bx) < 0.01 * 4095
    notes = "" if full else "absolute X map is not the full screen width;"
    s.log("absolute_supported", ax=ax, bx=bx, ay=ay, page_top=top)
    return replace(pm.cal, mode="absolute", abs_map=(round(ax, 5), round(bx, 3), round(ay, 5), 0.0),
                   method="safari", measured_at=time.strftime("%Y-%m-%d %H:%M:%S"),
                   notes=pm.cal.notes + notes, extra={**pm.cal.extra, "page_top_pt": round(top, 2)})


def _measure(s: _Session, W, H, inner_h, pos, rates: _Rates, coarse_counts, fine_counts, repeats,
             coarse_target: float = COARSE_TARGET_PT) -> PointerCalibration:
    pm = s.pm
    entry_x = 3
    spans = (W, inner_h)
    margins = (EDGE_MARGIN * W, EDGE_MARGIN * inner_h)
    centre = (W / 2, inner_h / 2)

    # 2. Run models from click-to-click differences, alternating directions around the middle. A run
    #    goes out only if the worst case of what was measured so far ends `margin` inside the page;
    #    otherwise the pointer first moves to where it would, and if no such place exists, no
    #    longer runs are measured along that axis.
    for axis in (0, 1):
        pos = _approach(s, pos, centre[axis], axis, spans[axis], margins[axis], rates)
    pos = _tune_steps(s, pos, W, centre, margins[0], rates, coarse_target)
    samples: dict[tuple[int, int, bool], list[tuple[int, float]]] = {}

    def fits(p, axis: int, fine: bool, n: int, sign: int) -> bool:
        room = (spans[axis] - p[axis] if sign > 0 else p[axis]) - margins[axis]
        return rates(axis, fine).upper(n) <= room

    for axis in (0, 1):
        span, margin = spans[axis], margins[axis]
        for fine, counts in ((False, coarse_counts), (True, fine_counts)):
            ok = True
            for n in counts:
                for _ in range(repeats):
                    for sign in (1, -1):
                        if not fits(pos, axis, fine, n, sign):
                            # start from where the run surely stays on the page, as near the middle as
                            # possible (a long run starts on one side and ends on the other)
                            u = rates(axis, fine).upper(n)
                            ok = u <= span - 2 * margin - 0.1 * span
                            if ok:
                                target = centre[axis] - sign * max(0.0, u + margin + 0.05 * span - span / 2)
                                pos = _approach(s, pos, target, axis, span, margin, rates,
                                                done=lambda p: fits(p, axis, fine, n, sign))
                                ok = fits(pos, axis, fine, n, sign)
                            if not ok:
                                break
                        before = pos
                        try:
                            pm.run(axis, 0 if fine else sign * n, sign * n if fine else 0)
                        except PointerDesync:  # paced badly: this sample is worthless, take a new reference
                            pos = s.click()
                            continue
                        pos = s.click()
                        moved = abs(pos[axis] - before[axis])
                        samples.setdefault((axis, sign, fine), []).append((n, moved))
                        rates(axis, fine).add(n, moved)
                        s.log("calibration_run", axis=axis, sign=sign, fine=fine, n=n, moved=round(moved, 2))
                    if not ok:
                        break
                if not ok:
                    break
    models = {}
    for (axis, sign), name in (((0, 1), "right"), ((0, -1), "left"), ((1, 1), "down"), ((1, -1), "up")):
        parts = []
        for fine in (False, True):
            got = samples.get((axis, sign, fine), [])
            if len({n for n, _ in got}) < 2:
                raise CalibrationError(f"could not measure {name} {'fine' if fine else 'coarse'} runs of two lengths "
                                       "that surely stay on the page (Tracking Speed very high or low?)")
            parts.append(_fit(got))
        if parts[0].a <= 0 or parts[1].a <= 0:
            raise CalibrationError(f"the {name} runs do not grow with their length: the pointer does not follow")
        models[name] = DirectionModel(*parts)
    cal = replace(pm.cal, **models)
    pm.cal = cal
    # one coarse run from the top anchor that lands well inside the page (below any top bar)
    down = cal.down.coarse
    y_entry = max(1, round((60 + 0.45 * inner_h - down.b) / down.a))
    entry_x = max(1, min(entry_x, round((0.1 * W - cal.right.coarse.b) / cal.right.coarse.a)))

    # 3. Edge offsets from anchored clicks (the models are known now).
    def anchored_click(sx: int, sy: int, xr: int, yr: int) -> tuple[float, float]:
        def go():
            pm.anchor(sx, sy)
            pm.run(0, xr, 0)
            pm.run(1, yr, 0)
            return s.click()
        return _retry(go)

    y_mid = cal.down.coarse.distance(y_entry)
    left, right, top = [], [], []
    for _ in range(3):
        p = anchored_click(-1, -1, entry_x, y_entry)
        left.append(p[0] - cal.right.coarse.distance(entry_x))
        top.append(y_mid - p[1])  # page offset, with the top edge at y = 0
        p = anchored_click(1, -1, -entry_x, y_entry)
        right.append(W - p[0] - cal.left.coarse.distance(entry_x))
    page_top = statistics.median(top)
    # bottom: anchor bottom-left and come up to about 70 % of the page
    up_target = H - (page_top + inner_h * 0.7)
    n_up = max(1, round((up_target - cal.up.coarse.b) / cal.up.coarse.a))
    bottom = []
    for _ in range(3):
        p = anchored_click(-1, 1, entry_x, -n_up)
        bottom.append(H - page_top - p[1] - cal.up.coarse.distance(n_up))
    edges = {"left": statistics.median(left), "right": statistics.median(right), "top": 0.0,
             "bottom": statistics.median(bottom)}
    spread = max(max(v) - min(v) for v in (left, right, top, bottom))
    cal = replace(cal, edges=tuple(round(edges[k], 2) for k in ("left", "top", "right", "bottom")),
                  extra={**cal.extra, "page_top_pt": round(page_top, 2), "anchor_spread_pt": round(spread, 2)})
    if abs(edges["left"]) > 2:
        cal.notes += f"left edge offset {edges['left']:.1f} pt: the top-edge = 0 assumption may be off by as much;"
    pm.cal = cal

    # 4. Shortest corner reset. The test starts far from the corner but no further down than the
    #    entry run allows, so even a reset that does not move at all leaves the test click on the
    #    page (never on Safari's bars); the count is then scaled to the full screen diagonal.
    entry_pt = (cal.right.coarse.distance(entry_x), y_mid)

    def probe_anchor(k: int | None) -> tuple[float, float]:
        if k is not None:
            pm.move_to(start[0], start[1])
            pm.cal = replace(pm.cal, reset_reports=k)
        try:
            pm.anchor(-1, -1)
        finally:
            pm.cal = replace(pm.cal, reset_reports=30)
        pm.run(0, entry_x, 0)
        pm.run(1, y_entry, 0)
        return s.click()

    start = (W * 0.9 - entry_pt[0], page_top + inner_h * 0.9 - entry_pt[1])
    ref = _retry(lambda: probe_anchor(None))
    reset = None
    for k in range(1, 30):
        p = _retry(lambda: probe_anchor(k))
        if abs(p[0] - ref[0]) <= 0.5 and abs(p[1] - ref[1]) <= 0.5:
            reset = k
            break
    if reset is None:
        raise CalibrationError("the pointer never reached the corner within 30 reports")
    scale = (W ** 2 + H ** 2) ** 0.5 / (start[0] ** 2 + start[1] ** 2) ** 0.5
    return replace(pm.cal, reset_reports=max(2 * reset, round(reset * scale) + 2), method="safari",
                   measured_at=time.strftime("%Y-%m-%d %H:%M:%S"))


def _resize(s: _Session, rates: _Rates, fine: bool, new: int | None = None) -> None:
    """Change the coarse (or fine) report size, halving it when `new` is None. What was measured at
    the old size then only bounds the new one."""
    cal = s.pm.cal
    old, lo = (cal.fine_step, 1) if fine else (cal.step, 4)
    if new is None:
        if old <= lo:
            raise CalibrationError("even one report of the smallest size might leave the page: lower the Tracking Speed")
        new = max(lo, old // 2)
        s.log("calibration_step", fine=fine, step=old, next=new, reason="one report might leave the page")
    s.pm.cal = replace(cal, **({"fine_step": new} if fine else {"step": new}))
    rates.resize(fine, old, new)


def _tune_steps(s: _Session, pos, W: float, centre, margin: float, rates: _Rates,
                coarse_target: float = COARSE_TARGET_PT, fine_target: float = FINE_TARGET_PT) -> tuple[float, float]:
    """Pick report sizes for this phone so a coarse report moves about 20 pt and a fine one about
    1 pt, whatever the Tracking Speed. Distance grows faster than the report size (acceleration),
    so the size is corrected a few times from measured runs along X around the page centre (3 coarse
    or 8 fine reports, fewer while that might leave the page)."""
    pm = s.pm
    for fine, target, lo, hi, most in ((False, coarse_target, 4, 100, 3), (True, fine_target, 1, 8, 8)):
        if fine:
            rates.seed_fine(pm.cal.step, pm.cal.fine_step)
        for _ in range(4):
            step = pm.cal.fine_step if fine else pm.cal.step
            sign = 1 if pos[0] < centre[0] else -1
            room = (W - pos[0] if sign > 0 else pos[0]) - margin
            n = max((k for k in range(1, most + 1) if rates(0, fine).upper(k) <= room), default=0)
            if n == 0:
                _resize(s, rates, fine)
                continue
            before = pos
            try:
                pm.run(0, 0 if fine else sign * n, sign * n if fine else 0)
            except PointerDesync:
                pos = s.click()
                continue
            pos = s.click()
            moved = abs(pos[0] - before[0])
            rates(0, fine).add(n, moved)
            per = moved / n
            if per <= 0:
                new = min(hi, step * 2)
            else:
                new = min(hi, max(lo, round(step * (target / per) ** 0.7)))
            s.log("calibration_step", fine=fine, step=step, n=n, per_report_pt=round(per, 2), next=new)
            if new == step or (per and abs(per - target) / target < 0.25):
                break
            _resize(s, rates, fine, new)
    return pos


def _approach(s: _Session, pos, target: float, axis: int, span: float, margin: float, rates: _Rates,
              done=None) -> tuple[float, float]:
    """Walk the pointer towards `target` along `axis` with coarse runs until `done(pos)` (by default:
    within 8 % of the span), learning what they cover: runs grow 1, 2, 4... reports, and each surely
    stops `margin` inside the page (see _Travel). When even one report might not, the report size
    is halved."""
    done = done or (lambda p: abs(target - p[axis]) < span * 0.08)
    for _ in range(16):
        d = target - pos[axis]
        if done(pos):
            break
        sign = 1 if d > 0 else -1
        room = (span - pos[axis] if sign > 0 else pos[axis]) - margin
        n = rates(axis).pick(abs(d), room)
        if n == 0:
            _resize(s, rates, False)
            continue
        before = pos
        try:
            s.pm.run(axis, sign * n, 0)
        except PointerDesync:  # moved an unknown part of the run (on the page): only take the new position
            pos = s.click()
            continue
        pos = s.click()
        rates(axis).add(n, abs(pos[axis] - before[axis]))
    return pos


def _validate(s: _Session, cal: PointerCalibration, W, inner_h, n: int, rng: random.Random,
              max_error: float = MAX_ERROR_PT) -> list[float]:
    """Planned moves to random points on the page, central ones first (a wrong model shows there
    before a target near an edge could put a click off the page); returns the landing errors in
    points, and refuses the calibration at the first one above `max_error`."""
    page_top = cal.extra["page_top_pt"]
    targets = [(rng.uniform(W * 0.05, W * 0.95), rng.uniform(inner_h * 0.1, inner_h * 0.9)) for _ in range(n)]
    targets.sort(key=lambda t: max(abs(t[0] / W - 0.5), abs(t[1] / inner_h - 0.5)))
    errors = []
    for x, y in targets:
        s.pm.move_to(x, page_top + y)  # retries internally on pacing problems
        p = s.click()
        err = math.dist(p, (x, y))
        errors.append(err)
        s.log("calibration_check", target=[round(x, 1), round(page_top + y, 1)], got=[p[0], round(p[1] + page_top, 1)],
              error_pt=round(err, 2))
        if err > max_error:
            raise CalibrationError(f"validation: a click landed {err:.1f} pt from its target (at most {max_error:g} pt "
                                   "accepted); calibrate again, with the phone untouched")
    return errors
