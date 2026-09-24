import pytest

from ihc.hid import protocol as p
from ihc.hid.base import HidStatusError, HidTimeout
from ihc.input.keyboard import Keyboard
from ihc.input.pointer import (
    DirectionModel,
    PointerCalibration,
    PointerDesync,
    PointerError,
    PointerModel,
    RunModel,
)
from ihc.sim.direct import VirtualClock


class Recorder:
    """HID stand-in that records reports and can fail on demand."""

    wait_ack = True

    def __init__(self, clock=None, wire_s=0.0):
        self.reports = []
        self.keys = []
        self.fail = []  # exceptions to raise on the next calls (then the call is recorded anyway)
        self.clock = clock
        self.wire_s = wire_s
        self.sent_at = []

    def _maybe_fail(self):
        if self.fail:
            raise self.fail.pop(0)

    def mouse_rel(self, dx, dy, buttons=0, wheel=0):
        if self.clock:
            self.sent_at.append(self.clock())
            self.clock.sleep(self.wire_s)
        self._maybe_fail()
        self.reports.append((dx, dy, buttons, wheel))

    def keyboard(self, mods, keys):
        self._maybe_fail()
        self.keys.append((mods, list(keys)))


def model(cal=None, **kwargs):
    clock = VirtualClock()
    rec = Recorder(clock)
    cal = cal or PointerCalibration(reset_reports=4)
    return PointerModel(rec, cal, clock=clock, sleep=clock.sleep, **kwargs), rec, clock


def test_run_model_and_plan():
    m = DirectionModel(RunModel(20.0, -5.0), RunModel(1.25, -0.1))
    assert m.coarse.distance(0) == 0 and m.coarse.distance(3) == 55.0
    nc, nf, got = m.plan(100.0)
    assert abs(got - 100.0) <= 0.7 and nc == 5
    assert m.plan(0) == (0, 0, 0.0)
    # small distances use fine reports only, and fine runs stay short
    nc, nf, got = m.plan(3.0)
    assert nc == 0 and abs(got - 3.0) < 0.7
    for d in range(1, 400, 7):
        nc, nf, got = m.plan(float(d))
        assert nf <= 22 and abs(got - d) <= 1.0


def test_pacing_is_fixed_rate_and_rests_before_runs():
    pm, rec, clock = model()
    pm.cal.interval = 0.02
    rec.wire_s = 0.004  # ack time must not stretch the interval
    pm.run(0, 3, 0)
    pm.run(0, 2, 0)
    t = rec.sent_at
    assert [round(b - a, 4) for a, b in zip(t, t[1:])] == [0.02, 0.02, pm.cal.rest, 0.02]


def test_clicks_do_not_wait_for_rest():
    pm, rec, clock = model()
    pm.run(0, 2, 0)
    t0 = clock()
    pm.press()
    assert clock() - t0 <= pm.cal.interval + 1e-9


def test_anchor_goes_to_nearest_corner_and_respects_edges():
    cal = PointerCalibration(reset_reports=3, edges=(0.5, 0.0, 1.0, 1.5), screen_pt=(400.0, 800.0))
    pm, rec, _ = model(cal)
    plan = pm.plan(300, 700)
    assert plan.anchor == (1, 1)
    assert pm.plan(100, 100).anchor == (-1, -1)
    pm.anchor(1, 1)
    assert rec.reports == [(127, 127, 0, 0)] * 3
    assert pm.position == (399.0, 798.5)


def test_move_to_runs_x_then_y_away_from_the_anchor():
    cal = PointerCalibration(reset_reports=2, screen_pt=(400.0, 800.0),
                             right=DirectionModel(RunModel(20, 0), RunModel(1, 0)),
                             left=DirectionModel(RunModel(20, 0), RunModel(1, 0)),
                             down=DirectionModel(RunModel(20, 0), RunModel(1, 0)),
                             up=DirectionModel(RunModel(20, 0), RunModel(1, 0)))
    pm, rec, _ = model(cal)
    plan = pm.move_to(345.0, 110.0)  # nearest corner: top-right
    assert plan.anchor == (1, -1)
    moves = [r for r in rec.reports if abs(r[0]) != 127]
    assert moves == [(-24, 0, 0, 0)] * 2 + [(-3, 0, 0, 0)] * 15 + [(0, 24, 0, 0)] * 5 + [(0, 3, 0, 0)] * 10
    assert plan.predicted_pt == (345.0, 110.0) and pm.position == (345.0, 110.0)


def test_move_to_rejects_outside_targets():
    pm, _, _ = model()
    with pytest.raises(PointerError):
        pm.move_to(-1, 10)


def test_anchor_refused_while_pressed():
    pm, _, _ = model()
    pm.press(p.MOUSE_LEFT)
    with pytest.raises(PointerError, match="drag"):
        pm.anchor(-1, -1)


def test_state_reports_are_resent_after_timeouts():
    pm, rec, _ = model()
    rec.fail = [HidTimeout("x")]
    pm.press()
    assert rec.reports[-1] == (0, 0, p.MOUSE_LEFT, 0) and pm.resends == 1


def test_damaged_frames_are_resent_even_for_moves():
    pm, rec, _ = model()
    rec.fail = [HidStatusError("bad sum", 5, p.Status.BAD_CHECKSUM)]
    pm.send(5, 0)
    assert rec.reports == [(5, 0, 0, 0)]


def test_lost_move_redoes_the_whole_move_from_the_anchor():
    pm, rec, _ = model(pipeline=False)
    pm.cal.reset_reports = 2
    rec.fail = [None] * 0
    calls = {"n": 0}
    original = rec.mouse_rel

    def flaky(dx, dy, buttons=0, wheel=0):
        calls["n"] += 1
        if calls["n"] == 4:  # the second movement report after the anchor
            raise HidTimeout("lost")
        original(dx, dy, buttons, wheel)

    rec.mouse_rel = flaky
    pm.move_to(250, 500)
    anchors = [i for i, r in enumerate(rec.reports) if abs(r[0]) == 127]
    assert len(anchors) == 4  # two anchor reports, twice: the move was redone
    assert pm.position is not None


def test_execution_errors_propagate():
    pm, rec, _ = model()
    rec.fail = [HidStatusError("not enumerated", 5, p.Status.EXEC_ERROR)]
    with pytest.raises(HidStatusError):
        pm.send(5, 0)
    assert pm.position is None


def test_late_report_is_detected():
    pm, rec, clock = model(pipeline=False)
    original = rec.mouse_rel

    def slow(dx, dy, buttons=0, wheel=0):
        original(dx, dy, buttons, wheel)
        if len(rec.reports) == 2:
            clock.sleep(0.05)  # host stalled

    rec.mouse_rel = slow
    with pytest.raises(PointerDesync, match="pacing"):
        pm.run(0, 4, 0)
    assert pm.timing_retries == 1 and pm.position is None


def test_pipelined_run_checks_every_ack():
    class Pipelined(Recorder):
        def __init__(self, clock):
            super().__init__(clock)
            self.stats = {"lost_acks": 0}
            self.async_errors = []
            self.modes = []

        def mouse_rel(self, dx, dy, buttons=0, wheel=0):
            self.modes.append(self.wait_ack)
            super().mouse_rel(dx, dy, buttons, wheel)

        def sync(self):
            if len(self.reports) == 3:
                self.stats["lost_acks"] += 1  # a reply that never came: retry from the anchor
            if len(self.reports) == 4:
                self.async_errors.append((5, 0xE4))  # damaged frame: also a retry
            if len(self.reports) == 5:
                self.async_errors.append((5, 0xE6))  # the chip refused: say why, do not retry

    clock = VirtualClock()
    hid = Pipelined(clock)
    pm = PointerModel(hid, PointerCalibration(reset_reports=2), clock=clock, sleep=clock.sleep)
    pm.run(0, 2, 0)
    assert hid.modes == [False, False] and hid.wait_ack is True
    with pytest.raises(PointerDesync, match="not acknowledged"):
        pm.run(0, 1, 0)
    with pytest.raises(PointerDesync):
        pm.run(0, 1, 0)
    with pytest.raises(HidStatusError, match="EXEC_ERROR"):
        pm.run(0, 1, 0)


def test_drag_and_raw_and_scroll():
    pm, rec, _ = model()
    pm.anchor(-1, -1)
    pm.press()
    pm.drag_by(0, 100)
    assert all(r[2] == p.MOUSE_LEFT for r in rec.reports[-5:])
    pm.release()
    pm.raw(5, -5)
    assert pm.position is None
    pm.scroll(-2)
    assert [r[3] for r in rec.reports[-2:]] == [-1, -1]


def test_calibration_json_roundtrip(tmp_path):
    cal = PointerCalibration(method="safari", edges=(1, 2, 3, 4), extra={"a": 1},
                             right=DirectionModel(RunModel(20.5, -8.3), RunModel(1.24, -0.13)))
    cal.save(tmp_path / "c.json")
    assert PointerCalibration.load(tmp_path / "c.json") == cal
    assert cal.calibrated and not PointerCalibration().calibrated


def test_keyboard_type_key_and_resend():
    rec = Recorder()
    kb = Keyboard(rec, sleep=lambda s: None)
    kb.type("Hi")
    kb.key("cmd+space")
    assert rec.keys == [(0x02, [0x0B]), (0, []), (0, [0x0C]), (0, []), (0, []), (0x08, [0x2C]), (0, [])]
    rec.fail = [HidTimeout("x")]
    kb.key("esc")
    assert rec.keys[-2:] == [(0, [0x29]), (0, [])] and kb.resends == 1
    with pytest.raises(ValueError):
        kb.type("é")
