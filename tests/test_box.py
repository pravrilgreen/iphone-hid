"""The all-in-one box: the board's own USB gadget as the HID device and its HDMI input as video,
found by zero-config discovery or given in a farm config."""

import sys

import pytest
from test_gadget import FakeNode
from test_pipe_video import WRITER, jpeg

from ihc import registry as reg_mod
from ihc.cli import main as ihc_main
from ihc.hid import gadget as g
from ihc.registry import NoVideo, auto, load_config
from ihc.rigs import ChipFound, GadgetFound, VideoFound, board_id, find_rigs, gadget_rig_id, pair_gadget

HDMI = VideoFound("/dev/video0", "rk_hdmirx", None)
CARD = VideoFound("/dev/video2", "MS2109", "1-1.3")


def gadget_found():
    return GadgetFound("ihc", "fc000000.usb", {fn: f"/dev/hidg{i}" for i, fn in enumerate(g.ORDER)}, "iphone-a1b2c3")


def test_gadget_takes_the_hdmi_input():
    spec = pair_gadget(gadget_found(), [CARD, HDMI])
    assert spec.id == "iphone-a1b2c3" and spec.video == HDMI and spec.chip is None
    assert pair_gadget(gadget_found(), [CARD]).video == CARD  # the only capture card
    assert pair_gadget(gadget_found(), [CARD, VideoFound("/dev/video4", "cam", "2-1")]).video is None
    assert "HID only" in pair_gadget(gadget_found(), []).notes[0]


def test_find_rigs_never_gives_the_hdmi_input_to_a_chip(monkeypatch):
    chip = ChipFound("/dev/ttyUSB0", 9600, 0, {"version": "V1.0"}, "1-1.2")
    monkeypatch.setattr("ihc.rigs.find_chips", lambda *a, **k: [chip])
    rigs = find_rigs(videos=lambda: [HDMI, CARD], gadget=gadget_found)
    assert [(r.id, r.video.device) for r in rigs] == [("iphone-a1b2c3", "/dev/video0"), ("rig-1-1.2", "/dev/video2")]
    no_gadget = find_rigs(videos=lambda: [HDMI], gadget=lambda: None)
    assert no_gadget[0].chip is chip and no_gadget[0].video == HDMI  # fallback: a CH9329 cable on the box
    in_use = find_rigs(videos=lambda: [HDMI], gadget=gadget_found, skip={__import__("os").path.realpath("gadget:ihc")})
    assert [r.id for r in in_use] == ["rig-1-1.2"]


def fake_backend_factory(tmp_path):
    state = tmp_path / "sys" / "class" / "udc" / "fc000000.usb"
    state.mkdir(parents=True, exist_ok=True)
    (state / "state").write_text("configured\n")
    made = []

    def factory(name="ihc", **kwargs):
        hid = g.GadgetBackend(name, nodes={fn: f"/dev/hidg{i}" for i, fn in enumerate(g.ORDER)}, udc="fc000000.usb",
                              sysfs=str(tmp_path / "sys"), open_node=lambda path, read=False: FakeNode(path, read=read))
        made.append(hid)
        return hid

    return factory, made


def test_auto_builds_the_box_rig(tmp_path, monkeypatch):
    factory, made = fake_backend_factory(tmp_path)
    monkeypatch.setattr(reg_mod, "GadgetBackend", factory)
    monkeypatch.setattr("ihc.rigs.find_videos", lambda sysfs="/sys": [HDMI])
    opened = []

    def open_video(device):
        opened.append(device)
        return NoVideo()

    reg = auto(state_dir=tmp_path, ports=[], sysfs=str(tmp_path / "nosys"), open_video=open_video, gadget=gadget_found)
    try:
        (dev,) = reg.devices()
        assert dev.id == "iphone-a1b2c3" and dev.hid is made[0] and opened == ["/dev/video0"]
        assert dev.calibration_path == tmp_path / "iphone-a1b2c3.json"
        h = dev.check()
        assert h["hid"] and h["usb_connected"]
        assert reg.rescan() == []  # the gadget in use is not found again
    finally:
        reg.close()


def test_config_with_gadget_and_a_jpeg_command(tmp_path, monkeypatch):
    factory, _ = fake_backend_factory(tmp_path)
    monkeypatch.setattr(reg_mod, "GadgetBackend", factory)
    frame = jpeg(90)
    script = tmp_path / "writer.py"
    script.write_text(WRITER)
    command = f"{sys.executable} {script} {frame.hex()}"
    (tmp_path / "farm.toml").write_text(f'''
[[device]]
id = "iphone"
model = "iPhone 15"
hid = {{ gadget = "ihc" }}
video = {{ device = "/dev/video0", command = "{command}" }}
''')
    reg = load_config(tmp_path / "farm.toml")
    try:
        dev = reg.get("iphone")
        assert dev.hid.port == "gadget:ihc"
        assert dev.source.latest(timeout=5).jpeg == frame
        assert dev.hid.info()["usb_connected"]
    finally:
        reg.close()


def test_hdmi_input_without_an_encoder_reports_why(tmp_path, monkeypatch):
    monkeypatch.setattr("ihc.video.pipe._gst_has", lambda element: False)
    monkeypatch.setattr("ihc.video.pipe.shutil.which", lambda name: None)
    src = reg_mod.open_video_source("/dev/video0", hdmi=True, edid="hdmi")
    try:
        with pytest.raises(TimeoutError):
            src.latest(timeout=0.5)
        assert "no JPEG encoder" in src.stats()["status"]
    finally:
        src.close()


def test_ihc_gadget_status(tmp_path, capsys):
    assert ihc_main(["gadget", "--configfs", str(tmp_path), "status"]) == 0
    assert "not set up" in capsys.readouterr().out


def test_capture_check_reads_an_hdmi_input_through_the_encoder(tmp_path, monkeypatch):
    from test_tools import load_tool

    capture_check = load_tool("capture_check")
    frame = jpeg(70)
    script = tmp_path / "writer.py"
    script.write_text(WRITER)
    calls = []
    monkeypatch.setattr("ihc.video.pipe.hdmi_in_command",
                        lambda device, fps=30, encoder="auto": calls.append((device, encoder)) or
                        [sys.executable, str(script), frame.hex()])
    monkeypatch.setattr("ihc.video.pipe.set_edid", lambda device, edid="hdmi": calls.append(("edid", edid)))
    r = capture_check.probe("/dev/video0", "MJPG", (1920, 1080), 30, 0.5, hdmi=True, encoder="gst", edid="hdmi")
    assert r["passthrough"] and r["frames"] > 3 and r["delivered_size"] == [64, 48]
    assert calls == [("edid", "hdmi"), ("/dev/video0", "gst")]


def test_each_box_names_its_phone_after_the_board(tmp_path, monkeypatch):
    serial = tmp_path / "serial-number"
    serial.write_bytes(b"f3c1a2b40d9e\x00")  # device-tree strings end with NUL
    machine = tmp_path / "machine-id"
    machine.write_text("0123456789abcdef\n")
    assert board_id([str(serial), str(machine)]) == "b40d9e"
    assert board_id([str(tmp_path / "none"), str(machine)]) == "abcdef"
    monkeypatch.setattr("ihc.rigs.board_id", lambda: "b40d9e")
    monkeypatch.delenv("IHC_PHONE_ID", raising=False)
    assert gadget_rig_id() == "iphone-b40d9e"
    assert pair_gadget(GadgetFound("ihc", "udc", {"keyboard": "/dev/hidg0"}), []).id == "iphone-b40d9e"
    monkeypatch.setenv("IHC_PHONE_ID", "iphone15-a01")
    assert gadget_rig_id() == "iphone15-a01"


def test_edid_is_set_for_a_custom_command_on_an_hdmi_input(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("ihc.video.pipe.set_edid", lambda device, edid="hdmi": calls.append((device, edid)))
    script = tmp_path / "writer.py"
    script.write_text(WRITER)
    src = reg_mod.open_video_source("/dev/video0", hdmi=True, command=f"{sys.executable} {script} {jpeg(40).hex()}")
    try:
        assert src.latest(timeout=5).jpeg == jpeg(40)
    finally:
        src.close()
    src = reg_mod.open_video_source("/dev/video9", command=f"{sys.executable} {script} {jpeg(40).hex()}")
    src.close()
    assert calls == [("/dev/video0", "hdmi")]  # not for a command on something that is not an HDMI input
