import os
from pathlib import Path

import pytest

from ihc.hid.config import ChipConfig
from ihc.hid.fake import FakeChip, FakeSerialDevice
from ihc.registry import NoVideo, auto
from ihc.rigs import ChipFound, VideoFound, find_videos, hub_of, pair, usb_port_of


def fake_sysfs(root: Path, ttys: dict, videos: dict) -> None:
    """ttys/videos: node name -> USB port path ("1-1.2"); videos value may be (port, index, name)."""
    devices = root / "devices" / "pci0000:00" / "0000:00:14.0" / "usb1"
    for name, port in ttys.items():
        d = devices / port / f"{port}:1.0" / name
        d.mkdir(parents=True)
        cls = root / "class" / "tty" / name
        cls.mkdir(parents=True)
        (cls / "device").symlink_to(d.parent)
    for name, (port, index, label) in videos.items():
        d = devices / port / f"{port}:1.0"
        d.mkdir(parents=True, exist_ok=True)
        cls = root / "class" / "video4linux" / name
        cls.mkdir(parents=True)
        (cls / "device").symlink_to(d)
        (cls / "index").write_text(f"{index}\n")
        (cls / "name").write_text(f"{label}\n")


def test_usb_ports_and_videos_from_sysfs(tmp_path):
    fake_sysfs(tmp_path, {"ttyUSB0": "1-1.2"}, {"video0": ("1-1.3", 0, "USB Video"), "video1": ("1-1.3", 1, "USB Video")})
    assert usb_port_of("/dev/ttyUSB0", str(tmp_path)) == "1-1.2"
    assert hub_of("1-1.2") == "1-1" and hub_of("1-4") is None
    vids = find_videos(str(tmp_path))
    assert [(v.device, v.usb_port) for v in vids] == [("/dev/video0", "1-1.3")]


def chip(port, usb):
    return ChipFound(port, 9600, 0, {"version": "V1.0"}, usb)


def test_pairing_by_hub_and_single_pair_fallback():
    rigs = pair([chip("/dev/ttyUSB0", "1-1.2"), chip("/dev/ttyUSB1", "1-2.1")],
                [VideoFound("/dev/video2", "cam", "1-2.4"), VideoFound("/dev/video0", "cam", "1-1.3")])
    assert [(r.id, r.video.device) for r in rigs] == [("rig-1-1.2", "/dev/video0"), ("rig-1-2.1", "/dev/video2")]
    lone = pair([chip("/dev/ttyUSB0", "1-4")], [VideoFound("/dev/video0", "cam", "2-1")])
    assert lone[0].video.device == "/dev/video0" and "only chip" in lone[0].notes[0]
    none = pair([chip("/dev/ttyUSB0", "1-4")], [])
    assert none[0].video is None and "HID only" in none[0].notes[0]


def test_auto_probes_chips_and_builds_devices(tmp_path):
    fast = FakeChip(ChipConfig.factory_default().replace(baud=115200))
    dev_a, dev_b = FakeSerialDevice(fast), FakeSerialDevice(FakeChip())
    silent_chip = FakeChip()
    silent_chip.powered = False
    silent = FakeSerialDevice(silent_chip)
    opened = []

    def open_video(device):
        opened.append(device)
        return NoVideo()

    try:
        reg = auto(state_dir=tmp_path, ports=[dev_a.port, dev_b.port, silent.port], sysfs=str(tmp_path / "nosys"),
                   probe_timeout=0.1, open_video=open_video)
        try:
            devices = reg.devices()
            assert len(devices) == 2  # the unpowered port is skipped
            bauds = sorted(d.hid.baud for d in devices)
            assert bauds == [9600, 115200]  # the baud rate was found automatically
            for d in devices:
                assert d.hid.info()["usb_connected"]
                assert d.calibration_path.parent == tmp_path
                d.check()
                assert d.state == "no_signal"  # HID only: no capture card
            assert opened == []
        finally:
            reg.close()
    finally:
        for d in (dev_a, dev_b, silent):
            d.close()


def test_hot_plugged_rig_is_added_by_rescan(tmp_path):
    first = FakeSerialDevice(FakeChip())
    ports = [first.port]
    later = None
    try:
        reg = auto(state_dir=tmp_path, ports=ports, sysfs=str(tmp_path / "nosys"), probe_timeout=0.1,
                   open_video=lambda d: NoVideo())
        try:
            assert len(reg.devices()) == 1
            assert reg.rescan() == []  # nothing new, and the port in use is not probed again
            later = FakeSerialDevice(FakeChip())
            ports.append(later.port)
            added = reg.rescan()
            assert len(added) == 1 and len(reg.devices()) == 2
            assert reg.get(added[0]).hid.info()["usb_connected"]
        finally:
            reg.close()
    finally:
        first.close()
        if later:
            later.close()


def test_mdns_roundtrip():
    pytest.importorskip("zeroconf")
    from ihc.discovery import advertise, discover

    ann = advertise(18765, name="ihc-test-box", devices=1)
    try:
        if not ann.active:
            pytest.skip("zeroconf could not start")
        urls = discover(1.5)
        if not urls:
            pytest.skip("no multicast on this network")
        assert any(u.endswith(":18765") for u in urls)
    finally:
        ann.close()
