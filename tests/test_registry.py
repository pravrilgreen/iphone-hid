from ihc.registry import config_from_discovery, discover, load_config


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

        def close(self):
            pass

    monkeypatch.setattr(capture, "V4L2Capture", NoVideo)
    dev = FakeSerialDevice(FakeChip())
    try:
        cfg = tmp_path / "farm.toml"
        cfg.write_text(
            "[[device]]\n"
            'id = "iphone-01"\n'
            'model = "iphone-15"\n'
            f'hid = {{ port = "{dev.port}", baud = 9600 }}\n'
            'video = { device = "/dev/video0", size = "1280x720", fps = 30 }\n'
            'calibration = "calib/iphone-01.json"\n'
        )
        reg = load_config(cfg)
        d = reg.get("iphone-01")
        assert d.info.model == "iPhone 15" and d.pointer.cal.screen_pt == (393.0, 852.0)
        assert d.source.kwargs["width"] == 1280 and d.hid.info()["usb_connected"]
        assert d.calibration_path == tmp_path / "calib" / "iphone-01.json"
        reg.close()
    finally:
        dev.close()
