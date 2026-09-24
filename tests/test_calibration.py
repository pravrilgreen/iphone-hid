"""Calibration through the Safari page, on the deterministic in-process simulator."""

import random
import statistics

import pytest

from ihc.calibration import CalibrationError, ClickCollector, calibrate
from ihc.input.pointer import PointerCalibration, PointerModel
from ihc.sim.direct import direct_phone
from ihc.sim.rig import exact_calibration

URL = "http://host:8000/calibrate/sim-01"


def setup(model="iphone-15", tracking=1.0, open_page=True):
    phone, chip, hid, clock = direct_phone(model)
    chip.pointer.tracking = tracking
    pm = PointerModel(hid, PointerCalibration(), clock=clock, sleep=clock.sleep)
    clicks = ClickCollector()
    phone.web_listeners.append(lambda url, ev: clicks.push(ev))
    if open_page:
        phone.open_url(URL)
    return phone, chip, pm, clicks, clock


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
    actions0 = len(phone.actions)
    cal = calibrate(pm, clicks, click_timeout=0.05)
    assert cal.method == "safari" and cal.calibrated
    assert cal.screen_pt == (phone.model.width_pt, phone.model.height_pt)
    # every click during calibration landed on the page, never on Safari's bars
    assert all(a.startswith("web") for a in phone.actions[actions0:])
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
    old = pm.cal
    phone.go_home()  # someone left Safari
    with pytest.raises(CalibrationError):
        calibrate(pm, clicks, click_timeout=0.05)
    assert pm.cal is old
