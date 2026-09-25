from ihc.registry import config_from_discovery, discover, load_config, simulated


def test_discover_pairs_devices_on_the_same_hub(tmp_path):
    serial, video = tmp_path / "serial", tmp_path / "v4l"
    serial.mkdir()
    video.mkdir()
    for name in ("pci-0000:00:14.0-usb-0:1.2:1.0-port0", "pci-0000:00:14.0-usb-0:2.1:1.0-port0",
                 "pci-0000:00:14.0-usb-0:4:1.0-port0"):
        (serial / name).touch()
    for name in ("pci-0000:00:14.0-usb-0:1.3:1.0-video-index0", "pci-0000:00:14.0-usb-0:1.3:1.0-video-index1",
                 "pci-0000:00:14.0-usb-0:2.4:1.0-video-index0"):
        (video / name).touch()
    rigs = discover(str(serial), str(video))
    assert [r.usb_path for r in rigs] == ["pci-0000:00:14.0-usb-0:1", "pci-0000:00:14.0-usb-0:2"]
    assert rigs[0].serial.endswith("1.2:1.0-port0") and rigs[0].video.endswith("1.3:1.0-video-index0")
    cfg = config_from_discovery(rigs, "iphone-15")
    assert cfg.count("[[device]]") == 2 and 'id = "iphone-02"' in cfg


def test_load_config_builds_devices(tmp_path, monkeypatch):
    from ihc.hid.fake import FakeChip, FakeSerialDevice
    import ihc.video.capture as capture

    class NoVideo:
        def __init__(self, device, **kwargs):
            self.device, self.kwargs = device, kwargs
            self.size = (kwargs.get("width", 0), kwargs.get("height", 0))

        def latest(self, newer_than=-1, timeout=1.0):
            raise TimeoutError("no video in this test")

        def close(self):
            pass

    monkeypatch.setattr(capture, "V4L2Capture", NoVideo)
    dev = FakeSerialDevice(FakeChip())
    try:
        cfg = tmp_path / "farm.toml"
        cfg.write_text(
            "[[device]]\n"
            'id = "iphone-01"\n'
            'model = "iPhone 15 Pro Max"\n'
            f'hid = {{ port = "{dev.port}", baud = 9600 }}\n'
            'video = { device = "/dev/video0", size = "1280x720", fps = 30 }\n'
            'calibration = "calib/iphone-01.json"\n'
            "[[device]]\n"
            'id = "iphone-02"\n'
            'model = "iphone-15"\n'
            'hid = { port = "/dev/serial/by-path/unplugged-port0" }\n'
            'video = { device = "/dev/video2" }\n'
        )
        reg = load_config(cfg)
        d = reg.get("iphone-01")
        assert d.info.model == "iPhone 15 Pro Max" and d.pointer.cal.screen_pt == (430.0, 932.0)
        assert d.source.kwargs["width"] == 1280 and d.hid.info()["usb_connected"]
        assert d.calibration_path == tmp_path / "calib" / "iphone-01.json"
        # an unplugged rig does not keep the farm from starting; it reports why
        missing = reg.get("iphone-02")
        h = missing.check()
        assert h["hid"] is False and "unplugged-port0" in h["error"] and missing.state == "hid_offline"
        reg.close()
    finally:
        dev.close()


def test_model_names_and_keys():
    from ihc.models import find_model, get_model

    assert find_model("iPhone 15").key == "iphone-15"
    assert find_model("iphone  16 PRO max").key == "iphone-16-pro-max"
    assert get_model("iphone-air").name == "iPhone Air"
    assert find_model("iPhone 13") is None  # no USB-C port
    assert find_model("Galaxy S24") is None


def test_serve_pointer_default(tmp_path):
    """serve --pointer: the mode of a phone not calibrated yet; a saved choice or a calibration wins."""
    from ihc.cli import default_pointer_mode

    reg = simulated(3, simulate_timing=False, calibrated=False)
    calibrated = simulated(1, simulate_timing=False)
    try:
        fresh, chosen, untouched = reg.devices()
        chosen.calibration_path = tmp_path / "chosen.json"
        chosen.set_pointer_mode("relative")  # e.g. picked in the console, kept in its file
        for dev in (fresh, chosen, calibrated.devices()[0]):
            default_pointer_mode(dev, "absolute")
        default_pointer_mode(untouched, None)
        modes = [d.status()["pointer"]["mode"] for d in (fresh, chosen, untouched, calibrated.devices()[0])]
        assert modes == ["absolute", "relative", "relative", "relative"]
    finally:
        reg.close()
        calibrated.close()
