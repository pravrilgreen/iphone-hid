"""Calibration through the Safari page, on the deterministic in-process simulator."""

import random
import statistics

import pytest

from ihc.calibration import CalibrationError, ClickCollector, calibrate
from ihc.input.pointer import PointerCalibration, PointerModel
from ihc.sim.direct import direct_phone
from ihc.sim.rig import exact_calibration

URL = "http://host:8000/calibrate/sim-01"


def setup(model="iphone-15", tracking=1.0, open_page=True, *, accel=None, network=None, cal=None, **phone_options):
    """A phone with the calibration page. `network(push)` returns what carries each page event to
    the host (default: every event, at once)."""
    phone, chip, hid, clock = direct_phone(model, **phone_options)
    chip.pointer.tracking = tracking
    if accel is not None:
        chip.pointer.accel = accel
    pm = PointerModel(hid, cal or PointerCalibration(), clock=clock, sleep=clock.sleep)
    clicks = ClickCollector()
    deliver = network(clicks.push) if network else clicks.push
    phone.web_listeners.append(lambda url, ev: deliver(ev))
    if open_page:
        phone.open_url(URL)
    return phone, chip, pm, clicks, clock


class Watch:
    """Every click the pointer makes, against the calibration page's rectangle on the screen."""

    def __init__(self, phone, chip):
        self.rect = phone.calibration_page_rect()
        self.clicks = []
        chip.listeners.append(lambda e: self.clicks.append((e["x"], e["y"])) if e["event"] == "click" else None)

    def check(self, misses_above: int = 8) -> int:
        """The safety invariant: no click off the page, except at most `misses_above` above it while
        looking for it (before any click landed on it). Returns how many there were."""
        left, top, right, bottom = self.rect
        above = 0
        for i, (x, y) in enumerate(self.clicks):
            if left <= x <= right and top <= y <= bottom:
                continue
            first_on_page = all(not (top <= yy <= bottom) for _, yy in self.clicks[:i])
            assert y < top and first_on_page, f"click {i} at ({x}, {y}) off the page {self.rect}"
            above += 1
        assert above <= misses_above, f"{above} clicks above the page"
        return above


# -- networks between the page and the host --------------------------------------------------------

def silent(push):
    """The network drops right after the page loaded."""
    return lambda ev: push(ev) if ev["type"] == "hello" and ev["seq"] == 1 else None


def lagging(in_order: bool, after: int = 0):
    """From the click after the first `after` ones, each click event arrives only when the next
    click happens; with `in_order` everything the page sent after it waits too (a stalled
    connection), otherwise only clicks are late."""
    def network(push):
        held, seen = [], [0]

        def carry(ev):
            seen[0] += ev["type"] == "click"
            if seen[0] <= after:
                push(ev)
            elif ev["type"] == "click":
                late = list(held)
                held.clear()
                held.append(ev)
                for e in late:
                    push(e)
            elif held and in_order:
                held.append(ev)
            else:
                push(ev)
        return carry
    return network


def dropping(rate: float, seed: int):
    def network(push):
        rng = random.Random(seed)
        return lambda ev: push(ev) if ev["seq"] == 1 or rng.random() >= rate else None
    return network


def legacy_of(network):
    """`network` carrying the events of an older page (no seq, no t, no heartbeat)."""
    def wrap(push):
        def strip(ev):
            push({k: v for k, v in ev.items() if k not in ("seq", "t", "heartbeat_ms")})

        inner = network(strip) if network else strip
        return lambda ev: None if ev["type"] == "hello" and ev["seq"] > 1 else inner(ev)
    return wrap


def tap_errors(phone, pm, clock, n=80, seed=0):
    phone.open_app("Targets")
    clock.sleep(1)
    rng = random.Random(seed)
    for _ in range(n):
        t = phone.targets[rng.randrange(len(phone.targets))]
        pm.move_to(t.x, t.y)
        pm.click()
    taps = [e for e in phone.tap_log if e["kind"] == "tap"]
    return [e["error_pt"] for e in taps], sum(e["hit"] for e in taps)


@pytest.mark.parametrize("model,tracking", [("iphone-15", 1.0), ("iphone-se-3", 0.4), ("iphone-15-pro-max", 2.5)])
def test_calibration_gives_accurate_taps(model, tracking):
    phone, chip, pm, clicks, clock = setup(model, tracking)
    watch = Watch(phone, chip)
    actions0, t0 = len(phone.actions), clock()
    cal = calibrate(pm, clicks, click_timeout=0.05)
    assert clock() - t0 < 55  # simulated seconds
    assert cal.method == "safari" and cal.calibrated
    assert cal.screen_pt == (phone.model.width_pt, phone.model.height_pt)
    # every click during calibration landed on the page (or above it while looking for it), never
    # on Safari's bars
    assert all(a.startswith("web") for a in phone.actions[actions0:])
    watch.check()
    assert cal.extra["validation_error_pt"]["max"] < 2.5
    errors, hits = tap_errors(phone, pm, clock)
    assert hits == len(errors) == 80
    assert statistics.median(errors) < 1.5 and max(errors) < 2.5


def test_measured_models_match_the_simulator():
    phone, chip, pm, clicks, clock = setup()
    cal = calibrate(pm, clicks, click_timeout=0.05)
    exact = exact_calibration(chip.pointer)
    for name in ("right", "left", "down", "up"):
        got, want = getattr(cal, name), getattr(exact, name)
        assert got.coarse.a == pytest.approx(want.coarse.a, rel=0.03)
        assert got.fine.a == pytest.approx(want.fine.a, rel=0.08)
    left, top, right, bottom = cal.edges
    assert abs(left) < 0.5 and abs(right - 1.0) < 0.5 and abs(bottom - 1.0) < 1.0
    assert cal.extra["page_top_pt"] == pytest.approx(phone.top, abs=1.0)
    assert 2 <= cal.reset_reports <= 20


def test_exact_calibration_is_already_accurate():
    phone, chip, pm, clicks, clock = setup(open_page=False)
    pm.cal = exact_calibration(chip.pointer)
    errors, hits = tap_errors(phone, pm, clock, n=60)
    assert hits == 60 and max(errors) < 1.0


def test_page_not_open_is_reported():
    phone, chip, pm, clicks, clock = setup(open_page=False)
    with pytest.raises(CalibrationError, match="did not load"):
        calibrate(pm, clicks, page_timeout=0.05)
    assert not pm.cal.calibrated


def test_page_closed_midway_restores_the_old_calibration():
    phone, chip, pm, clicks, clock = setup()
    watch = Watch(phone, chip)
    old = pm.cal
    phone.go_home()  # someone left Safari
    with pytest.raises(CalibrationError):
        calibrate(pm, clicks, click_timeout=0.05)
    assert pm.cal is old
    assert len(watch.clicks) == 1  # the page's heartbeats stopped: no second click on the home screen
    assert phone.screen == "home"


@pytest.mark.parametrize("accel", [5, 7, 10])
@pytest.mark.parametrize("tracking", [0.4, 1.0, 2.5])
def test_strong_acceleration_never_clicks_off_the_page(accel, tracking):
    phone, chip, pm, clicks, clock = setup(tracking=tracking, accel=accel)
    watch = Watch(phone, chip)
    cal = calibrate(pm, clicks, click_timeout=0.05)
    watch.check()
    assert cal.extra["validation_error_pt"]["max"] < 2.0


@pytest.mark.parametrize("tracking", [0.25, 5.0, 8.0])
def test_extreme_tracking_speeds(tracking):
    phone, chip, pm, clicks, clock = setup(tracking=tracking)
    watch = Watch(phone, chip)
    cal = calibrate(pm, clicks, click_timeout=0.05)
    watch.check()
    assert cal.extra["validation_error_pt"]["max"] < 2.0


def test_hover_finds_the_page_without_a_single_miss():
    phone, chip, pm, clicks, clock = setup(web_hover=True)
    watch = Watch(phone, chip)
    cal = calibrate(pm, clicks, click_timeout=0.05)
    assert watch.check(misses_above=0) == 0
    assert cal.extra["hover_events"] is True


def test_no_events_after_the_page_loaded():
    """The review case: the network drops after the page loaded. The page's heartbeats stop, so
    the first click without an answer stops the calibration: no walk down the screen."""
    phone, chip, pm, clicks, clock = setup(network=silent)
    watch = Watch(phone, chip)
    old = pm.cal
    with pytest.raises(CalibrationError, match="no event"):
        calibrate(pm, clicks, click_timeout=0.05)
    assert pm.cal is old
    assert len(watch.clicks) == 1
    watch.check()


def test_old_page_without_seq_still_calibrates():
    phone, chip, pm, clicks, clock = setup(network=legacy_of(None))
    watch = Watch(phone, chip)
    cal = calibrate(pm, clicks, click_timeout=0.05)
    watch.check()
    assert cal.extra["validation_error_pt"]["max"] < 2.0


def test_old_page_without_events_stops_above_the_floor():
    """An older page cannot prove a miss: the way down is bounded by the most a report may cover,
    and it stops well before the bottom of the page."""
    phone, chip, pm, clicks, clock = setup(network=legacy_of(silent))
    watch = Watch(phone, chip)
    with pytest.raises(CalibrationError, match="no click reached"):
        calibrate(pm, clicks, click_timeout=0.05)
    watch.check()
    left, top, right, bottom = watch.rect
    assert 2 <= len(watch.clicks) <= 8
    assert max(y for _, y in watch.clicks) < top + 0.85 * (bottom - top)


@pytest.mark.parametrize("after", [0, 30])
@pytest.mark.parametrize("in_order,old_page", [(False, False), (True, False), (False, True), (True, True)],
                         ids=["clicks late", "all late", "old page, clicks late", "old page, all late"])
def test_late_events_are_never_taken_for_the_next_click(in_order, old_page, after):
    network = lagging(in_order, after)
    phone, chip, pm, clicks, clock = setup(network=legacy_of(network) if old_page else network)
    watch = Watch(phone, chip)
    with pytest.raises(CalibrationError):
        calibrate(pm, clicks, click_timeout=0.05)
    watch.check()
    assert len(watch.clicks) <= after + 8


@pytest.mark.parametrize("seed", range(4))
@pytest.mark.parametrize("old_page", [False, True])
def test_lost_events_never_lead_off_the_page(seed, old_page):
    network = dropping(0.2, seed)
    phone, chip, pm, clicks, clock = setup(network=legacy_of(network) if old_page else network)
    watch = Watch(phone, chip)
    try:
        cal = calibrate(pm, clicks, click_timeout=0.05)
    except CalibrationError:
        pass
    else:
        assert cal.extra["validation_error_pt"]["max"] < 2.0
    watch.check()


def test_absolute_mode():
    phone, chip, pm, clicks, clock = setup()
    chip.pointer.absolute = True
    watch = Watch(phone, chip)
    t0 = clock()
    cal = calibrate(pm, clicks, click_timeout=0.05)
    assert clock() - t0 < 12
    assert cal.mode == "absolute" and cal.extra["validation_error_pt"]["max"] < 0.5
    # the glide takes 60 ms here: the measured wait covers it without the 250 ms default
    assert chip.pointer.abs_glide <= cal.abs_settle <= 0.15
    assert cal.extra["page_top_pt"] == pytest.approx(phone.top, abs=1.0)
    watch.check()
    errors, hits = tap_errors(phone, pm, clock, n=40)
    assert hits == 40 and max(errors) < 1.0


@pytest.mark.parametrize("scale,offset", [(0.9, 60.0), (1.1, -40.0), (1.0, 30.0)])
def test_absolute_y_map_that_is_not_the_screen_falls_back_to_relative(scale, offset):
    """The page offset cannot be told from page clicks when the absolute Y range is not the screen
    height (review: it used to 'succeed' 10 pt off): the relative models are measured instead."""
    phone, chip, pm, clicks, clock = setup()
    ptr = chip.pointer
    ptr.absolute = True
    real = ptr.absolute_report
    ptr.absolute_report = lambda ax, ay, *rest: real(ax, min(4095, max(0, round(ay * scale + offset / ptr.height * 4095))), *rest)
    watch = Watch(phone, chip)
    cal = calibrate(pm, clicks, click_timeout=0.05)
    assert cal.mode == "relative" and cal.extra["validation_error_pt"]["max"] < 2.0
    watch.check()
    errors, hits = tap_errors(phone, pm, clock, n=40)
    assert hits == 40


@pytest.mark.parametrize("step,tracking", [(100, 3.0), (60, 2.0), (24, 8.0)])
def test_report_size_shrinking_during_tuning(step, tracking):
    """Review: the rate measured before the step was tuned went stale and every coarse run was
    refused, then a KeyError. The rates now follow the step size."""
    phone, chip, pm, clicks, clock = setup(tracking=tracking, cal=PointerCalibration(step=step))
    watch = Watch(phone, chip)
    cal = calibrate(pm, clicks, click_timeout=0.05)
    assert cal.step < 24
    watch.check()


def test_runs_that_cannot_stay_on_the_page_are_an_error():
    phone, chip, pm, clicks, clock = setup()
    old = pm.cal
    with pytest.raises(CalibrationError, match="two lengths"):
        calibrate(pm, clicks, click_timeout=0.05, fine_counts=(4, 400))
    assert pm.cal is old


def test_validation_error_above_the_limit_is_refused():
    phone, chip, pm, clicks, clock = setup()
    old = pm.cal
    with pytest.raises(CalibrationError, match="validation"):
        calibrate(pm, clicks, click_timeout=0.05, max_error=0.05)
    assert pm.cal is old


def test_page_resized_midway_stops_the_calibration():
    def shrinking(push):
        n = [0]

        def carry(ev):
            n[0] += 1
            if ev["type"] == "hello" and n[0] > 40:
                ev = {**ev, "inner_h": ev["inner_h"] - 290}  # the keyboard came up
            push(ev)
        return carry

    phone, chip, pm, clicks, clock = setup(network=shrinking)
    watch = Watch(phone, chip)
    with pytest.raises(CalibrationError, match="changed size"):
        calibrate(pm, clicks, click_timeout=0.05)
    watch.check()


def test_collector_keeps_events_in_order():
    c = ClickCollector(maxlen=5)
    c.push({"type": "hello", "screen_w": 393, "screen_h": 852, "inner_w": 393, "inner_h": 714})
    c.push({"type": "move", "x": 1, "y": 2})
    c.push({"type": "click", "x": 3, "y": 4})
    assert c.wait_page(0)["inner_h"] == 714 and c.moves == 1
    assert c.next_click(0)["x"] == 3 and c.next_click(0) is None
    for i in range(8):
        c.push({"type": "hello", "seq": i})
    assert [e["seq"] for e in c.take()] == [3, 4, 5, 6, 7] and c.take() == []


def test_absolute_settle_follows_the_glide():
    """A slow glide needs a longer wait before clicking: calibration measures it (clicks sent too
    early land short, on the page)."""
    phone, chip, pm, clicks, clock = setup()
    chip.pointer.absolute = True
    chip.pointer.abs_glide = 0.2
    watch = Watch(phone, chip)
    cal = calibrate(pm, clicks, click_timeout=0.05)
    assert cal.mode == "absolute" and 0.2 <= cal.abs_settle <= 0.4
    watch.check()
    errors, hits = tap_errors(phone, pm, clock, n=20)
    assert hits == 20 and max(errors) < 1.0


def test_looking_for_the_page_stays_right_of_the_status_bar_middle():
    """Clicks above the page land right of the Dynamic Island: the status bar's left side can hold
    the "◀ Search" return link after a Spotlight launch."""
    phone, chip, pm, clicks, clock = setup()
    watch = Watch(phone, chip)
    calibrate(pm, clicks, click_timeout=0.05)
    top = phone.calibration_page_rect()[1]
    above = [(x, y) for x, y in watch.clicks if y < top]
    assert above and all(x > 0.66 * phone.model.width_pt for x, _ in above)
    watch.check()


def test_recalibrating_ignores_the_old_page_load():
    """The previous page load's last heartbeat reaches the new collector first: the calibration
    follows the page opened for it, not the old one (review m3)."""
    phone, chip, pm, clicks, clock = setup(open_page=False)
    clicks.push({"type": "hello", "pid": "old-load", "seq": 912, "t": 450_000.0, "screen_w": 393, "screen_h": 852,
                 "inner_w": 393, "inner_h": 714, "heartbeat_ms": 250})
    phone.open_url(URL)
    cal = calibrate(pm, clicks, click_timeout=0.05, fresh_page=True)
    assert cal.method == "safari" and cal.extra["validation_error_pt"]["max"] < 2.0


def test_page_loaded_again_midway_stops_the_calibration():
    phone, chip, pm, clicks, clock = setup()
    seen = [0]

    def reload_after_some_clicks(e):
        if e["event"] == "click":
            seen[0] += 1
            if seen[0] == 6:
                phone.open_url(URL)

    chip.listeners.append(reload_after_some_clicks)
    with pytest.raises(CalibrationError, match="loaded again"):
        calibrate(pm, clicks, click_timeout=0.05)
