
import numpy as np
import pytest

from ihc.hid import protocol as p
from ihc.hid.fake import FakeChip
from ihc.input import keymap
from ihc.sim.hdmi import SimHdmiCapture
from ihc.models import MODELS
from ihc.sim.phone import SimPhone


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def rig():
    clock = Clock()
    chip = FakeChip()
    chip.pointer.clock = clock
    phone = SimPhone(MODELS["iphone-15"], open_delay=0.3, clock=clock)
    phone.attach(chip)
    return chip, phone, clock


def click_at(chip, x, y, buttons=p.MOUSE_LEFT):
    chip.pointer.x, chip.pointer.y = x, y
    chip.receive(p.mouse_rel(0, 0, buttons))
    chip.receive(p.mouse_rel(0, 0, 0))


def type_text(chip, text):
    for mods, keys in keymap.text_reports(text):
        chip.receive(p.kb_general(mods, keys))


def combo(chip, name):
    chip.receive(p.kb_general(*keymap.parse_combo(name)))
    chip.receive(p.kb_general(0, []))


def test_tap_icon_opens_app_after_animation(rig):
    chip, phone, clock = rig
    notes = phone.find("Notes")
    v0 = phone.version
    click_at(chip, *notes.center)
    assert phone.screen == "Notes" and phone.version > v0
    assert phone.changed_at == pytest.approx(clock() + 0.3)


def test_notes_compose_type_done(rig):
    chip, phone, _ = rig
    phone.open_app("Notes")
    click_at(chip, *phone.find("Compose").center)
    type_text(chip, "hello\nworld")
    chip.receive(p.kb_general(0, [keymap.KEYS["backspace"]]))
    chip.receive(p.kb_general(0, []))
    click_at(chip, *phone.find("Done").center)
    assert phone.notes[0] == "hello\nworl"


def test_right_and_middle_buttons(rig):
    chip, phone, _ = rig
    phone.open_app("Settings")
    click_at(chip, 100, 100, p.MOUSE_RIGHT)
    assert phone.screen == "home"
    click_at(chip, 100, 100, p.MOUSE_MIDDLE)
    assert phone.overlay == "switcher"
    card = phone.find("Settings")
    click_at(chip, *card.center)
    assert phone.screen == "Settings" and phone.overlay is None


def test_spotlight_search_and_enter(rig):
    chip, phone, _ = rig
    combo(chip, "cmd+space")
    assert phone.overlay == "spotlight"
    type_text(chip, "targ")
    assert phone.spotlight_results() == ["Targets"]
    type_text(chip, "\n")
    assert phone.screen == "Targets" and phone.overlay is None


def test_esc_closes_spotlight(rig):
    chip, phone, _ = rig
    combo(chip, "cmd+space")
    combo(chip, "esc")
    assert phone.overlay is None


def test_swipe_up_from_bottom_goes_home(rig):
    chip, phone, _ = rig
    phone.open_app("Maps")
    pt = chip.pointer
    pt.x, pt.y = 196, 845
    chip.receive(p.mouse_rel(0, 0, p.MOUSE_LEFT))
    pt.y = 600
    chip.receive(p.mouse_rel(0, 0, p.MOUSE_LEFT))
    chip.receive(p.mouse_rel(0, 0, 0))
    assert phone.screen == "home"


def test_settings_scroll_with_wheel_and_drag(rig):
    chip, phone, _ = rig
    phone.open_app("Settings")
    chip.receive(p.mouse_rel(0, 0, 0, -5))
    assert phone.scroll["Settings"] > 0
    first = phone.layout().elements[1].label
    assert first != "Airplane Mode"
    chip.receive(p.mouse_rel(0, 0, 0, 20))
    assert phone.scroll["Settings"] == 0


def test_targets_log_hits_and_misses(rig):
    chip, phone, _ = rig
    phone.open_app("Targets")
    t = phone.targets[5]
    click_at(chip, t.x + 1, t.y)
    click_at(chip, t.x + t.r + 5, t.y)
    log = [e for e in phone.tap_log if e["kind"] == "tap"]
    assert [e["hit"] for e in log] == [True, False]
    assert log[0]["error_pt"] == pytest.approx(1.0, abs=0.1)
    assert phone.target_center_norm(5) == (t.x / 393, t.y / 852)


def test_long_press_is_recorded(rig):
    chip, phone, clock = rig
    phone.open_app("Targets")
    t = phone.targets[0]
    chip.pointer.x, chip.pointer.y = t.x, t.y
    chip.receive(p.mouse_rel(0, 0, p.MOUSE_LEFT))
    clock.t += 0.8
    chip.receive(p.mouse_rel(0, 0, 0))
    assert phone.tap_log[-1]["long"] is True


def test_lock_and_accessory_prompt_disconnect_hid(rig):
    chip, phone, _ = rig
    phone.lock()
    assert chip.usb_connected is False
    (reply,) = p.FrameParser().feed(chip.receive(p.mouse_rel(1, 0)))
    assert reply.status == p.Status.EXEC_ERROR
    phone.unlock()
    assert chip.usb_connected
    phone.prompt_accessory()
    assert not chip.usb_connected and phone.layout().overlay[0].kind == "alert"
    phone.allow_accessory()
    assert chip.usb_connected


def test_volume_keys_show_hud(rig):
    chip, phone, clock = rig
    chip.receive(p.kb_media(p.MEDIA_KEYS["volume_up"]))
    assert phone.volume > 0.5 and phone.layout().hud
    clock.t += 2
    assert not phone.layout().hud


def test_capture_geometry_latency_and_range(rig):
    chip, phone, clock = rig
    cap = SimHdmiCapture(phone, chip.pointer, fps=30, latency=0.1, clock=clock, sleep=lambda s: setattr(clock, "t", clock.t + s))
    assert cap.size == (1920, 1080)
    r = cap.screen_rect
    assert (r.w, r.h) == (498.0, 1080.0) and r.x == pytest.approx(710.5)
    f = cap.latest()
    img = f.image
    assert f.jpeg is not None and img.shape == (1080, 1920, 3)
    assert 10 <= int(np.median(img[:, :600])) <= 22  # limited-range black bars
    x_before = cap.pointer_truth()[0]
    chip.receive(p.mouse_rel(60, 0))
    assert cap.pointer_truth()[0] == x_before  # not visible yet: capture latency
    clock.t += 0.11
    assert cap.pointer_truth()[0] > x_before + 20
    f2 = cap.latest(newer_than=f.seq)
    assert f2.seq == f.seq + 1 and f2.ts >= f.ts + 1 / 30 - 1e-9


def test_capture_shows_old_screen_until_animation_done(rig):
    chip, phone, clock = rig
    cap = SimHdmiCapture(phone, chip.pointer, latency=0.0, pointer_visible=False, clock=clock,
                         sleep=lambda s: setattr(clock, "t", clock.t + s))
    home = cap.latest().image.copy()
    phone.open_app("Settings")
    clock.t += 0.05
    during = cap.latest(newer_than=0).image
    assert np.abs(during.astype(int) - home.astype(int)).mean() < 1.0
    clock.t += 0.5
    after = cap.latest(newer_than=1).image
    assert np.abs(after.astype(int) - home.astype(int)).mean() > 10


def test_no_signal_is_black(rig):
    chip, phone, clock = rig
    cap = SimHdmiCapture(phone, chip.pointer, clock=clock, sleep=lambda s: None)
    cap.signal = False
    assert int(cap.latest().image.max()) <= 20


@pytest.mark.parametrize("key", sorted(MODELS))
def test_every_model_renders(key):
    chip = FakeChip()
    phone = SimPhone(MODELS[key])
    phone.attach(chip)
    cap = SimHdmiCapture(phone, chip.pointer, size=(1280, 720))
    f = cap.latest()
    rect = cap.screen_rect
    assert f.size == (1280, 720)
    assert abs(rect.w / rect.h - MODELS[key].aspect) < 0.01
