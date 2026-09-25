"""IPhoneDevice over the real driver on a pty (simulated phones, 9600-baud timing off for speed)."""

import time

import cv2
import numpy as np
import pytest

from ihc.hid.base import HidError
from ihc.registry import simulated


@pytest.fixture(scope="module")
def farm():
    reg = simulated(2, simulate_timing=False)
    abs_reg = simulated(1, simulate_timing=False, absolute=True)
    yield reg, abs_reg
    reg.close()
    abs_reg.close()


def rig_of(reg, i=0):
    dev = reg.devices()[i]
    return dev, reg.extra(dev.id)


def open_targets(rig):
    rig.phone.open_app("Targets")
    time.sleep(0.45)


@pytest.mark.parametrize("which", ["relative", "absolute"])
def test_taps_hit_targets(farm, which):
    reg = farm[0] if which == "relative" else farm[1]
    dev, rig = rig_of(reg)
    open_targets(rig)
    # Through the pty the simulated chip inherits this host's thread scheduling, which jitters the
    # report timing that relative mode depends on; exact accuracy is covered by test_calibration
    # on the virtual clock. Here: the real stack must land on the target.
    for index in (0, 6, 12, 20, 26):  # the large targets
        res = dev.tap(*rig.phone.target_center_norm(index))
        tap = rig.phone.tap_log[-1]
        assert tap["hit"] and tap["target"] == index, (res, tap)
    assert dev.status()["pointer"]["mode"] == which


def test_tap_in_frame_coordinates(farm):
    dev, rig = rig_of(farm[0], 1)
    rig.phone.go_home()
    time.sleep(0.45)
    el = rig.phone.find("Notes")
    rect = dev.screen_rect()
    fx, fy = rect.norm_to_frame(el.center[0] / rig.phone.model.width_pt, el.center[1] / rig.phone.model.height_pt)
    dev.tap(fx, fy, space="frame")
    assert rig.phone.screen == "Notes"


def test_keyboard_buttons_and_swipe(farm):
    dev, rig = rig_of(farm[0], 1)
    phone = rig.phone
    dev.home()
    assert phone.screen == "home"
    dev.key("cmd+space")
    dev.type("sett\n")
    assert phone.screen == "Settings"
    time.sleep(0.45)
    before = phone.scroll["Settings"]
    dev.swipe(0.5, 0.75, 0.5, 0.35)
    assert phone.scroll["Settings"] > before
    dev.scroll(0.5, 0.5, 30)
    assert phone.scroll["Settings"] == 0
    dev.app_switcher()
    assert phone.overlay == "switcher"
    dev.home()
    dev.media("volume_up")
    assert phone.volume > 0.5


def test_open_url_opens_safari(farm):
    dev, rig = rig_of(farm[0], 1)
    dev.open_url("http://example.org/x")
    assert rig.phone.screen == "Safari" and rig.phone.url == "http://example.org/x"
    dev.home()


def test_live_control(farm):
    dev, rig = rig_of(farm[0])
    pt = rig.chip.pointer
    x0 = pt.x
    dev.live_mouse(40, 0, 0)
    assert pt.x > x0 and dev.pointer.position is None
    dev.live_keys(0x02, [0x04])
    dev.live_keys(0, [])
    dev.release_all()
    assert rig.chip.keyboard.pressed == set() and pt.buttons == 0
    adev, arig = rig_of(farm[1])
    adev.live_abs(0.25, 0.5, 0)
    time.sleep(0.1)
    x, y = arig.chip.pointer.current()
    assert abs(x - 0.25 * 393) < 1.5 and abs(y - 0.5 * 852) < 1.5


def test_screenshot_status_and_health(farm):
    dev, rig = rig_of(farm[0])
    img = cv2.imdecode(np.frombuffer(dev.screenshot("png"), np.uint8), cv2.IMREAD_COLOR)
    assert img.shape[:2] == (1080, 498)
    assert dev.screenshot("jpeg", crop=False)[:2] == b"\xff\xd8"
    assert dev.check() == {"hid": True, "usb_connected": True, "signal": True, "error": None}
    s = dev.status()
    assert s["state"] == "ready" and s["screen"]["rect"]["w"] == 498
    assert s["calibration"]["calibrated"] and s["hid"]["stats"]["tx"] > 0


def test_state_follows_lock_and_signal():
    reg = simulated(1, simulate_timing=False)
    try:
        dev, rig = rig_of(reg)
        rig.phone.lock()
        dev.check()
        assert dev.state == "hid_disconnected"
        with pytest.raises(HidError):
            dev.tap(0.5, 0.5)
        assert dev.counters["errors"] == 1 and dev.last_result["ok"] is False
        rig.phone.unlock()
        rig.capture.signal = False
        dev.check()
        assert dev.state == "no_signal"
        rig.capture.signal = True
        time.sleep(0.05)  # next frame
        dev.check()
        assert dev.state == "ready"
    finally:
        reg.close()


def test_calibration_through_the_simulated_safari():
    reg = simulated(1, simulate_timing=False, calibrated=False, absolute=True)
    try:
        dev, rig = rig_of(reg)
        assert not dev.pointer.cal.calibrated
        res = dev.calibrate(page_url=f"http://host:8000/calibrate/{dev.id}", click_timeout=0.3)
        assert res["calibration"]["method"] == "safari"
        assert dev.status()["pointer"]["mode"] == "absolute"
        open_targets(rig)
        dev.tap(*rig.phone.target_center_norm(12))
        assert rig.phone.tap_log[-1]["hit"]
    finally:
        reg.close()


def test_absolute_pointer_without_calibration(tmp_path):
    """A phone seen to follow absolute reports (abstest) is driven in absolute mode straight away:
    the whole report range over the whole screen, kept in the calibration file."""
    reg = simulated(1, simulate_timing=False, calibrated=False, absolute=True)
    try:
        dev, rig = rig_of(reg)
        dev.calibration_path = tmp_path / "cal.json"
        assert dev.set_pointer_mode("absolute") == {"mode": "absolute"}
        assert not dev.pointer.cal.calibrated and dev.status()["pointer"]["mode"] == "absolute"
        assert '"mode": "absolute"' in dev.calibration_path.read_text()
        open_targets(rig)
        for index in (0, 12, 26):
            dev.tap(*rig.phone.target_center_norm(index))
            tap = rig.phone.tap_log[-1]
            assert tap["hit"] and tap["target"] == index, tap
        with pytest.raises(ValueError):
            dev.set_pointer_mode("diagonal")
        dev.set_pointer_mode("relative")
        assert dev.status()["pointer"]["mode"] == "relative"
    finally:
        reg.close()


def test_failed_actions_leave_nothing_held(farm):
    from ihc.hid.base import HidStatusError
    from ihc.hid import protocol as p

    dev, rig = rig_of(farm[0], 1)
    original = dev.pointer.drag_by

    def broken_drag(*args, **kwargs):
        raise HidStatusError("phone locked", p.Cmd.SEND_MS_REL_DATA, p.Status.EXEC_ERROR)

    dev.pointer.drag_by = broken_drag
    try:
        with pytest.raises(HidError):
            dev.swipe(0.5, 0.5, 0.5, 0.3)
    finally:
        dev.pointer.drag_by = original
    assert rig.chip.pointer.buttons == 0 and dev.pointer.buttons == 0
    rig.chip.fail_next = [p.Status.EXEC_ERROR]  # the media press is refused
    with pytest.raises(HidError):
        dev.media("volume_up")
    assert dev.state != "busy"


def test_frozen_capture_is_reported(farm):
    dev, rig = rig_of(farm[0], 1)

    class Frozen:
        def __init__(self, source):
            self.source = source
            self.size = source.size

        def latest(self, newer_than=-1, timeout=1.0):
            return self.source.latest(newer_than, timeout)

        def stats(self):
            return {"age_s": 5.0, "status": "ok"}

        def close(self):
            pass

    real = dev.source
    dev.source = Frozen(real)
    try:
        h = dev.check()
        assert h["signal"] is False and "no new frame" in h["error"] and dev.state == "no_signal"
    finally:
        dev.source = real
    assert dev.check()["signal"] is True


def test_release_after_the_chip_comes_back():
    from ihc.hid.base import HidTimeout

    reg = simulated(1, simulate_timing=False, timeout=0.15)
    try:
        dev, rig = rig_of(reg)
        original = dev.pointer.drag_by

        died = []

        def chip_dies(*args, **kwargs):
            rig.chip.powered = False  # e.g. the phone side lost power with the button down
            died.append(time.monotonic())
            raise HidTimeout("gone")

        dev.pointer.drag_by = chip_dies
        with pytest.raises(HidError):
            dev.swipe(0.5, 0.5, 0.5, 0.3)
        assert time.monotonic() - died[0] < 1.2  # no long chain of doomed releases
        dev.pointer.drag_by = original
        assert rig.chip.pointer.buttons == 1 and dev._release_pending
        rig.chip.powered = True
        dev.check()  # the monitor's next round
        assert rig.chip.pointer.buttons == 0 and not dev._release_pending
    finally:
        reg.close()


def test_simulated_signal_cut_shows_at_once(farm):
    dev, rig = rig_of(farm[0], 1)
    assert dev.check()["signal"] is True
    rig.capture.signal = False
    try:
        assert dev.check()["signal"] is False
    finally:
        rig.capture.signal = True


def test_releases_when_the_phone_starts_taking_input(farm):
    """A held key or button can survive a restart, a replug or a lock: the first health check that
    finds the phone taking input releases everything."""
    dev, rig = rig_of(farm[0], 1)
    dev.check()
    rig.chip.keyboard.report(0, [0x04])  # held from before (e.g. the previous process died)
    rig.chip.usb_connected = False  # locked
    assert dev.check()["usb_connected"] is False
    rig.chip.usb_connected = True  # unlocked
    dev.check()
    assert not rig.chip.keyboard.pressed and not dev._release_pending


def test_failed_calibration_keeps_the_old_model(monkeypatch):
    import ihc.device as device_mod
    from ihc.input.pointer import PointerCalibration

    reg = simulated(1, simulate_timing=False)
    try:
        dev, rig = rig_of(reg)
        installed = PointerCalibration(interval=0.03, method="safari")
        dev.pointer.cal = installed

        def failing(pm, clicks, **options):
            assert pm.cal.interval == PointerCalibration.interval  # measured at the default pace...
            raise RuntimeError("page lost")

        monkeypatch.setattr(device_mod, "calibrate", failing)
        with pytest.raises(RuntimeError):
            dev.calibrate()
        assert dev.pointer.cal is installed  # ...but a failure leaves the installed model untouched
        assert dev.state == "ready"
    finally:
        reg.close()
