import pytest

from ihc.hid import scan
from ihc.hid.config import ChipConfig
from ihc.hid.fake import FakeChip, FakeSerialDevice


@pytest.fixture
def device():
    made = []

    def make(chip):
        dev = FakeSerialDevice(chip)
        made.append(dev)
        return dev

    yield make
    for dev in made:
        dev.close()


def test_finds_non_default_baud(device):
    dev = device(FakeChip(ChipConfig.factory_default().replace(baud=38400)))
    results = scan.scan_port(dev.port, timeout=0.1)
    assert [r.baud for r in results] == [9600, 115200, 57600, 38400]
    assert results[-1].ok and results[-1].info["usb_connected"]
    assert scan.advice(results)[0] == f"use: --port {dev.port} --baud 38400"


def test_unpowered_chip(device):
    chip = FakeChip()
    chip.powered = False
    dev = device(chip)
    results = scan.scan_port(dev.port, bauds=[9600, 115200], timeout=0.1)
    assert not any(r.ok for r in results)
    assert "no reply at any baud rate" in scan.advice(results)[0]


def test_garbage_is_reported(device):
    chip = FakeChip(ChipConfig.factory_default().replace(baud=57600))
    chip.noise_on_mismatch = True
    dev = device(chip)
    results = scan.scan_port(dev.port, bauds=[9600], timeout=0.1)
    assert results[0].garbage and "garbage" in results[0].summary()


def test_address_sweep(device):
    dev = device(FakeChip(ChipConfig.factory_default().replace(address=0x05)))
    (r,) = scan.scan_port(dev.port, bauds=[9600], timeout=0.1, scan_addr=True)
    assert r.ok and r.addr == 0x05
    assert "--addr 0x05" in scan.advice([r])[0]


def test_usb_side_not_connected_advice(device):
    dev = device(FakeChip(usb_connected=False))
    results = scan.scan_port(dev.port, bauds=[9600], timeout=0.1)
    assert "not enumerated" in scan.advice(results)[1]


def test_candidate_ports_prefers_known_bridges():
    ports = [
        scan.PortInfo("/dev/ttyS0", "n/a", None, None, None),
        scan.PortInfo("/dev/ttyUSB1", "FT232", 0x0403, 0x6001, None),
        scan.PortInfo("/dev/ttyACM0", "thing", 0x1234, 0x5678, None),
    ]
    assert [pi.device for pi in scan.candidate_ports(ports)] == ["/dev/ttyUSB1", "/dev/ttyACM0"]


def test_main_on_fake_port(device, capsys, tmp_path):
    dev = device(FakeChip())
    log = tmp_path / "scan.jsonl"
    assert scan.main(["--port", dev.port, "--timeout", "0.1", "--log", str(log)]) == 0
    assert "9600 baud: OK" in capsys.readouterr().out
    assert '"event": "scan_done"' in log.read_text()


def test_main_list(capsys):
    assert scan.main(["--list"]) == 0
