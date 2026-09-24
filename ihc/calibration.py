"""Pointer calibration through a web page opened in Safari on the phone (no computer vision).

The page (web/calibrate.html) reports the page coordinates of every click. From clicks alone:
- run models: the distance a run covers from rest is the difference between the click before it and
  the click after it, so no page offset is involved. Runs alternate in direction around the middle
  of the page, so the pointer never touches an edge while measuring;
- edge offsets: where an anchored pointer really sits (iOS may clamp it one point inside an edge).
  Left and right come straight from the page (it spans the screen width). Vertically the page is
  shifted by the status bar and Safari's bars by an unknown offset; the top edge is taken as 0
  (the pointer's hot spot clamps at the edge; the left edge measurement checks that assumption), which
  gives the page offset, and the bottom edge follows;
- reset length: the fewest corner-slam reports that still reach the corner from the far side.
- validation: planned moves to random points, with the landing error.

Absolute pointer reports are tried first, once the pointer sits safely on the page: if clicks land
where absolute reports put them, the phone follows absolute positioning and only the grid-to-screen
map is measured (seconds instead of a minute). Otherwise the relative models above are measured.
"""

from __future__ import annotations

import queue
import random
import statistics
import threading
import time
from dataclasses import replace

from .input.pointer import DirectionModel, PointerCalibration, PointerDesync, PointerModel, RunModel


class CalibrationError(RuntimeError):
    pass


class ClickCollector:
    """Events posted by the calibration page (over HTTP, or directly by the simulator)."""

    def __init__(self) -> None:
        self._clicks: queue.Queue[dict] = queue.Queue()
        self._hello = threading.Event()
        self.page: dict | None = None
        self.moves = 0  # pointermove events seen: tells whether Safari reports hover positions

    def push(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "hello":
            self.page = event
            self._hello.set()
        elif kind == "click":
            self._clicks.put(event)
        elif kind == "move":
            self.moves += 1

    def clear(self) -> None:
        while not self._clicks.empty():
            self._clicks.get_nowait()

    def wait_page(self, timeout: float) -> dict:
        if not self._hello.wait(timeout):
            raise CalibrationError("the calibration page did not load on the phone")
        return self.page

    def next_click(self, timeout: float) -> dict | None:
        try:
            return self._clicks.get(timeout=timeout)
        except queue.Empty:
            return None


def _fit(samples: list[tuple[int, float]]) -> RunModel:
    """Least squares d = a * n + b."""
    if len({n for n, _ in samples}) < 2:
        raise CalibrationError("need runs of at least two lengths to fit a model")
    ns = [n for n, _ in samples]
    ds = [d for _, d in samples]
    mn, md = statistics.fmean(ns), statistics.fmean(ds)
    var = sum((n - mn) ** 2 for n in ns)
    a = sum((n - mn) * (d - md) for n, d in samples) / var
    return RunModel(round(a, 4), round(md - a * mn, 4))


def _retry(fn, attempts: int = 4):
    """Run an anchored probe again when its pacing was off (the anchor makes it repeatable)."""
    for i in range(attempts):
        try:
            return fn()
        except PointerDesync:
            if i == attempts - 1:
                raise


class _Session:
    def __init__(self, pm: PointerModel, clicks: ClickCollector, timeout: float, log):
        self.pm = pm
        self.clicks = clicks
        self.timeout = timeout
        self.log = log or (lambda event, **fields: None)
        self.count = 0

    def click(self) -> tuple[float, float] | None:
        """Click where the pointer is and return the page coordinates the page saw (None: missed
        the page, e.g. on the status bar)."""
        self.clicks.clear()
        self.pm.click()
        self.pm.rest()
        ev = self.clicks.next_click(self.timeout)
        self.count += 1
        return (float(ev["x"]), float(ev["y"])) if ev else None

    def must_click(self) -> tuple[float, float]:
        pos = self.click()
        if pos is None:
            raise CalibrationError("a click did not reach the calibration page; is it still open and unscrolled?")
        return pos


def calibrate(
    pm: PointerModel,
    clicks: ClickCollector,
    *,
    coarse_counts: tuple[int, ...] = (1, 2, 3, 4, 6, 8),
    fine_counts: tuple[int, ...] = (2, 4, 8, 12, 16),
    repeats: int = 2,
    validate: int = 8,
    try_absolute: bool = True,
    page_timeout: float = 15.0,
    click_timeout: float = 2.0,
    seed: int = 0,
    log=None,
) -> PointerCalibration:
    """Measure `pm`'s phone with the calibration page already open (or opening) in Safari.
    On success the new calibration is installed in `pm` and returned."""
    page = clicks.wait_page(page_timeout)
    W, H = float(page["screen_w"]), float(page["screen_h"])
    inner_w, inner_h = float(page["inner_w"]), float(page["inner_h"])
    if abs(inner_w - W) > 1:
        raise CalibrationError(f"the page is {inner_w:.0f} pt wide but the screen {W:.0f} pt: use portrait, no zoom")
    old = pm.cal
    pm.cal = replace(old, screen_pt=(W, H), reset_reports=max(old.reset_reports, 30), mode="relative")
    s = _Session(pm, clicks, click_timeout, log)
    try:
        pos = _enter_page(s, inner_h)
        cal = _try_absolute(s, W, H, inner_h) if try_absolute else None
        if cal is None:
            pos = s.must_click()  # the absolute test may have moved the pointer (on the page)
            cal = _measure(s, W, H, inner_h, pos, coarse_counts, fine_counts, repeats)
        pm.cal = cal
        errors = _validate(s, cal, W, H, inner_h, validate, random.Random(seed))
    except Exception:
        pm.cal = old
        raise
    cal.extra.update({
        "validation_error_pt": {"mean": round(statistics.fmean(errors), 2), "max": round(max(errors), 2), "n": len(errors)}
        if errors else None,
        "clicks": s.count,
        "hover_events": clicks.moves > 0,
        "user_agent": page.get("ua", ""),
    })
    s.log("calibrated", **cal.to_dict())
    return cal


def _enter_page(s: _Session, inner_h: float) -> tuple[float, float]:
    """Anchor top-left, then step down (1, 2, then 4 reports at a time) until a click lands on the
    page. Growing slowly keeps the pointer off Safari's bottom bar."""
    pm = s.pm
    _retry(lambda: pm.anchor(-1, -1))
    pm.run(0, 3, 0)
    k = 1
    for _ in range(80):
        try:
            pm.run(1, k, 0)
        except PointerDesync:
            pass
        pos = s.click()
        if pos is not None:
            return pos
        k = min(2 * k, 4)
    raise CalibrationError("could not reach the calibration page: is it open in Safari, in portrait?")


def _abs_click(s: _Session, gx: int, gy: int) -> tuple[float, float] | None:
    pm = s.pm
    pm.send_abs(gx, gy)
    pm._sleep(0.25)  # generous: iOS glides the cursor to an absolute position
    pm.buttons = 1
    pm.send_abs(gx, gy)
    pm._sleep(0.06)
    pm.buttons = 0
    pm.send_abs(gx, gy)
    ev = s.clicks.next_click(s.timeout)
    s.count += 1
    return (float(ev["x"]), float(ev["y"])) if ev else None


def _try_absolute(s: _Session, W, H, inner_h) -> PointerCalibration | None:
    """Absolute reports to a 3x3 grid of points on the page. None when the phone does not follow
    them (no click at all, or clicks where the pointer already was)."""
    from .hid.base import HidError

    pm = s.pm
    page_top_guess = H - inner_h - 84  # status bar height is unknown; only used to aim at the page
    samples = []
    for fx in (0.25, 0.5, 0.75):
        for fy in (0.3, 0.5, 0.7):
            y = page_top_guess + fy * inner_h
            gx, gy = round(fx * 4095), round(y / H * 4095)
            try:
                got = _abs_click(s, gx, gy)
            except HidError as e:
                s.log("absolute_unsupported", error=str(e))
                return None
            if got is None or abs(got[0] - fx * W) > 0.1 * W:
                s.log("absolute_unsupported", target=[gx, gy], got=got)
                return None
            samples.append((gx, gy, got))
    # grid = a * points + b; X straight from the page (it spans the screen width)
    fit_x = _fit([(g, x) for g, _, (x, _) in samples])  # points = a' * grid + b'
    ax, bx = 1 / fit_x.a, -fit_x.b / fit_x.a
    full = abs(ax - 4095 / W) < 0.03 * 4095 / W and abs(bx) < 0.01 * 4095
    # Y: the page offset is unknown; the grid is assumed to span the screen height like X does
    ay, by = 4095 / H, 0.0
    page_top = statistics.fmean(gy / ay - y for _, gy, (_, y) in samples)
    notes = "" if full else "absolute X map is not the full screen width; Y assumed full height;"
    s.log("absolute_supported", ax=ax, bx=bx, page_top=page_top)
    return replace(pm.cal, mode="absolute", abs_map=(round(ax, 5), round(bx, 3), round(ay, 5), round(by, 3)),
                   method="safari", measured_at=time.strftime("%Y-%m-%d %H:%M:%S"),
                   notes=pm.cal.notes + notes, extra={**pm.cal.extra, "page_top_pt": round(page_top, 2)})


def _measure(s: _Session, W, H, inner_h, pos, coarse_counts, fine_counts, repeats) -> PointerCalibration:
    pm = s.pm
    entry_x = 3

    # 2. Run models from click-to-click differences, alternating directions around the middle,
    #    with runs never longer than 35 % of the page so no edge is touched.
    centre = (W / 2, inner_h / 2)
    rate: dict[int, float] = {}  # rough points per coarse report, per axis
    for axis, span in ((0, W), (1, inner_h)):
        pos = _approach(s, pos, centre[axis], axis, span, rate)
    pos = _tune_steps(s, pos, W, centre)
    samples: dict[tuple[int, int, bool], list[tuple[int, float]]] = {}

    def predicted(axis: int, fine: bool, n: int) -> float:
        """Longest distance n reports may cover, from what was measured so far."""
        got = samples.get((axis, 1, fine), []) + samples.get((axis, -1, fine), [])
        if not got:
            return 0.0 if fine else rate.get(axis, 0.0) * n * 1.5
        return max(d / k for k, d in got) * n * 1.2

    for axis, span in ((0, W), (1, inner_h)):
        for fine, counts in ((False, coarse_counts), (True, fine_counts)):
            for n in counts:
                if predicted(axis, fine, n) > span * 0.35:
                    break  # would risk leaving the page (and clicking Safari's bars)
                too_long = False
                for _ in range(repeats):
                    for sign in (1, -1):
                        before = pos
                        try:
                            pm.run(axis, sign * n if not fine else 0, sign * n if fine else 0)
                        except PointerDesync:  # paced badly: this sample is worthless, take a new reference
                            pos = s.must_click()
                            continue
                        pos = s.must_click()
                        moved = abs(pos[axis] - before[axis])
                        samples.setdefault((axis, sign, fine), []).append((n, moved))
                        s.log("calibration_run", axis=axis, sign=sign, fine=fine, n=n, moved=round(moved, 2))
                        too_long |= moved > span * 0.35
                if abs(pos[axis] - centre[axis]) > span * 0.15:
                    pos = _approach(s, pos, centre[axis], axis, span, rate)
                if too_long:
                    break
    models = {}
    for (axis, sign) in ((0, 1), (0, -1), (1, 1), (1, -1)):
        models[(axis, sign)] = DirectionModel(_fit(samples[(axis, sign, False)]), _fit(samples[(axis, sign, True)]))
    cal = replace(pm.cal, right=models[(0, 1)], left=models[(0, -1)], down=models[(1, 1)], up=models[(1, -1)])
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
            return s.must_click()
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

    def probe_anchor(k: int | None) -> tuple[float, float] | None:
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
    if ref is None:
        raise CalibrationError("the reference click after a full corner reset missed the page")
    reset = None
    for k in range(1, 30):
        p = _retry(lambda: probe_anchor(k))
        if p is not None and abs(p[0] - ref[0]) <= 0.5 and abs(p[1] - ref[1]) <= 0.5:
            reset = k
            break
    if reset is None:
        raise CalibrationError("the pointer never reached the corner within 30 reports")
    scale = (W ** 2 + H ** 2) ** 0.5 / (start[0] ** 2 + start[1] ** 2) ** 0.5
    return replace(pm.cal, reset_reports=max(2 * reset, round(reset * scale) + 2), method="safari",
                   measured_at=time.strftime("%Y-%m-%d %H:%M:%S"))


COARSE_TARGET_PT = 20.0  # travel per coarse report: fast, while a fine run still covers one of them
FINE_TARGET_PT = 1.0  # travel per fine report: the resolution of a tap


def _tune_steps(s: _Session, pos, W: float, centre) -> tuple[float, float]:
    """Pick report sizes for this phone so a coarse report moves about 20 pt and a fine one about
    1 pt, whatever the Tracking Speed. Distance grows faster than the report size (acceleration),
    so the size is corrected a few times from measured runs along X around the page centre."""
    pm = s.pm
    for fine, target, lo, hi, n in ((False, COARSE_TARGET_PT, 4, 100, 3), (True, FINE_TARGET_PT, 1, 8, 8)):
        for _ in range(4):
            step = pm.cal.fine_step if fine else pm.cal.step
            sign = 1 if pos[0] < centre[0] else -1
            before = pos
            try:
                pm.run(0, 0 if fine else sign * n, sign * n if fine else 0)
            except PointerDesync:
                pos = s.must_click()
                continue
            pos = s.must_click()
            per = abs(pos[0] - before[0]) / n
            if per <= 0:
                new = min(hi, step * 2)
            else:
                new = min(hi, max(lo, round(step * (target / per) ** 0.7)))
            s.log("calibration_step", fine=fine, step=step, per_report_pt=round(per, 2), next=new)
            if new == step or (per and abs(per - target) / target < 0.25):
                break
            pm.cal = replace(pm.cal, **({"fine_step": new} if fine else {"step": new}))
    return pos


def _approach(s: _Session, pos, target: float, axis: int, span: float, rate: dict[int, float]) -> tuple[float, float]:
    """Walk the pointer towards `target` along `axis` with coarse runs, never more than 30 % of
    the page per run, learning the points-per-report rate from each click."""
    for _ in range(12):
        d = target - pos[axis]
        if abs(d) < span * 0.08:
            break
        sign = 1 if d > 0 else -1
        r = rate.get(axis)
        n = 1 if r is None else max(1, min(int(abs(d) * 0.8 / r), int(span * 0.3 / r)))
        before = pos
        try:
            s.pm.run(axis, sign * n, 0)
        except PointerDesync:
            pass
        pos = s.must_click()
        moved = abs(pos[axis] - before[axis])
        if moved > 0:
            rate[axis] = max(rate.get(axis, 0.0), moved / n)
    return pos


def _validate(s: _Session, cal: PointerCalibration, W, H, inner_h, n: int, rng: random.Random) -> list[float]:
    """Planned moves to random points on the page; returns the landing errors in points."""
    page_top = cal.extra["page_top_pt"]
    errors = []
    for _ in range(n):
        x = rng.uniform(W * 0.05, W * 0.95)
        y = page_top + rng.uniform(inner_h * 0.1, inner_h * 0.9)
        s.pm.move_to(x, y)  # retries internally on pacing problems
        p = s.must_click()
        err = ((p[0] - x) ** 2 + (p[1] + page_top - y) ** 2) ** 0.5
        errors.append(err)
        s.log("calibration_check", target=[round(x, 1), round(y, 1)], got=[p[0], round(p[1] + page_top, 1)], error_pt=round(err, 2))
    return errors
