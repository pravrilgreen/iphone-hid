"""Linux USB gadget backend: descriptors, configfs set-up and tear-down, delivery accounting.

Real f_hid nodes need a USB device controller, so the backend runs here over FakeNode, which
behaves like f_hid (one report in flight; taken when the "phone" polls), plus one test of the real
node class over a FIFO for the os-level code."""

import errno
import os
import shutil
from pathlib import Path

import pytest

from ihc.hid import gadget as g
from ihc.hid import protocol as p
from ihc.hid.base import HidPortError, HidStatusError, HidTimeout
from ihc.input.pointer import PointerCalibration, PointerModel

# -- report descriptors ---------------------------------------------------------------------------


def parse_descriptor(desc: bytes) -> dict:
    """Walk the short items of a report descriptor: collection depth and input/output bits."""
    i, depth, size, count = 0, 0, 0, 0
    bits = {"input": 0, "output": 0}
    while i < len(desc):
        prefix = desc[i]
        n = (0, 1, 2, 4)[prefix & 0x03]
        data = int.from_bytes(desc[i + 1:i + 1 + n], "little")
        assert i + 1 + n <= len(desc), "item runs past the end"
        tag, typ = prefix & 0xFC, (prefix >> 2) & 0x03
        if tag == 0xA0:
            depth += 1
        elif tag == 0xC0:
            depth -= 1
            assert depth >= 0
        elif tag == 0x74:
            size = data
        elif tag == 0x94:
            count = data
        elif tag == 0x80:
            bits["input"] += size * count
        elif tag == 0x90:
            bits["output"] += size * count
        elif tag == 0x84:
            raise AssertionError("no report IDs: every collection has its own interface")
        assert typ != 3, "long items are not used"
        i += 1 + n
    assert depth == 0, "unbalanced collections"
    return bits


@pytest.mark.parametrize("fn", list(g.FUNCTIONS))
def test_descriptor_matches_report_length(fn):
    spec = g.FUNCTIONS[fn]
    bits = parse_descriptor(spec.descriptor)
    assert bits["input"] == spec.report_length * 8
    assert bits["output"] == (8 if spec.out_reports else 0)  # keyboard LEDs only


def test_consumer_usages_in_ch9329_bit_order():
    assert len(g.CONSUMER_USAGES) == 24
    assert g.CONSUMER_USAGES[:4] == (0x0E9, 0x0EA, 0x0E2, 0x0CD)  # volume up/down, mute, play/pause
    assert p.MEDIA_KEYS["volume_up"] == 1 and p.MEDIA_KEYS["mute"] == 4


def test_abs_scaling_rounds_to_nearest():
    assert [g.scale_abs(v) for v in (0, 1, 2048, 4095)] == [0, 8, 16388, 32767]


# -- configfs -------------------------------------------------------------------------------------


@pytest.fixture
def fs(tmp_path, monkeypatch):
    configfs = tmp_path / "usb_gadget"
    configfs.mkdir()
    sysfs = tmp_path / "sys"
    (sysfs / "class" / "udc" / "fc000000.usb").mkdir(parents=True)
    (sysfs / "class" / "udc" / "fc000000.usb" / "state").write_text("not attached\n")
    monkeypatch.setattr(g, "_rmdir", lambda path: shutil.rmtree(path))  # real configfs dirs hold attributes
    return configfs, sysfs


def test_up_writes_functions_in_interface_order(fs):
    configfs, sysfs = fs
    udc = g.gadget_up("ihc", "RA", configfs=str(configfs), sysfs=str(sysfs))
    assert udc == "fc000000.usb"
    gd = configfs / "ihc"
    assert (gd / "UDC").read_text() == "fc000000.usb"
    assert (gd / "idVendor").read_text() == "0x1d6b"
    assert (gd / "strings" / "0x409" / "serialnumber").read_text() == "ihc-RA"
    kb = gd / "functions" / "hid.keyboard"
    assert (kb / "report_desc").read_bytes() == g.DESC_KEYBOARD
    assert (kb / "protocol").read_text() == "1" and (kb / "report_length").read_text() == "8"
    assert g.gadget_functions("ihc", str(configfs)) == ["keyboard", "consumer", "mouse", "absolute"]
    link = gd / "configs" / "c.1" / "hid.absolute"
    assert link.is_symlink() and os.readlink(link) == str(gd / "functions" / "hid.absolute")


def test_profile_without_relative_mouse(fs):
    configfs, sysfs = fs
    g.gadget_up("ihc", "A", configfs=str(configfs), sysfs=str(sysfs))
    assert g.gadget_functions("ihc", str(configfs)) == ["keyboard", "consumer", "absolute"]
    assert (configfs / "ihc" / "strings" / "0x409" / "serialnumber").read_text() == "ihc-A"  # a new identity


def test_up_refuses_what_it_cannot_do(fs):
    configfs, sysfs = fs
    with pytest.raises(g.GadgetError, match="unknown profile"):
        g.gadget_up("ihc", "XYZ", configfs=str(configfs), sysfs=str(sysfs))
    with pytest.raises(g.GadgetError, match="no USB device controller"):
        g.gadget_up("ihc", configfs=str(configfs), sysfs=str(sysfs / "nothing"))
    (sysfs / "class" / "udc" / "other.usb").mkdir()
    with pytest.raises(g.GadgetError, match="several"):
        g.gadget_up("ihc", configfs=str(configfs), sysfs=str(sysfs))
    shutil.rmtree(sysfs / "class" / "udc" / "other.usb")
    adb = configfs / "g1"
    adb.mkdir()
    (adb / "UDC").write_text("fc000000.usb\n")
    with pytest.raises(g.GadgetError, match="already used by the gadget 'g1'"):
        g.gadget_up("ihc", configfs=str(configfs), sysfs=str(sysfs))
    assert not (configfs / "ihc").exists()
    shutil.rmtree(adb)
    g.gadget_up("ihc", configfs=str(configfs), sysfs=str(sysfs))
    with pytest.raises(g.GadgetError, match="already exists"):
        g.gadget_up("ihc", configfs=str(configfs), sysfs=str(sysfs))
    with pytest.raises(g.GadgetError, match="configfs|not found"):
        g.gadget_up("ihc", configfs=str(configfs / "missing"), sysfs=str(sysfs))


def test_down_unbinds_and_removes(fs):
    configfs, sysfs = fs
    g.gadget_up("ihc", configfs=str(configfs), sysfs=str(sysfs))
    assert g.gadget_down("ihc", configfs=str(configfs))
    assert not (configfs / "ihc").exists()
    assert not g.gadget_down("ihc", configfs=str(configfs))


def test_nodes_and_status(fs, tmp_path):
    configfs, sysfs = fs
    g.gadget_up("ihc", "K", configfs=str(configfs), sysfs=str(sysfs))
    (configfs / "ihc" / "functions" / "hid.keyboard" / "dev").write_text("236:0\n")
    (sysfs / "dev" / "char" / "236:0").mkdir(parents=True)
    (sysfs / "dev" / "char" / "236:0" / "uevent").write_text("MAJOR=236\nMINOR=0\nDEVNAME=hidg0\n")
    assert g.gadget_nodes("ihc", str(configfs), str(sysfs), "/dev") == {"keyboard": "/dev/hidg0"}
    (sysfs / "class" / "udc" / "fc000000.usb" / "state").write_text("configured\n")
    st = g.gadget_status("ihc", str(configfs), str(sysfs))
    assert st["udc"] == "fc000000.usb" and st["state"] == "configured" and st["functions"] == ["keyboard"]


# -- the backend ----------------------------------------------------------------------------------


class FakeNode:
    """Like an f_hid node: one report in flight, taken when the phone polls."""

    def __init__(self, path, *, read=False):
        self.path = path
        self.read = read
        self.pending = False
        self.lost = False
        self.polling = True  # the phone takes each report at once
        self.inflight = None
        self.taken: list[bytes] = []
        self.out_reports: list[bytes] = []
        self.error: int | None = None
        self.closed = False

    def write(self, report):
        if self.error is not None:
            raise OSError(self.error, os.strerror(self.error))
        if self.inflight is not None:
            raise BlockingIOError(errno.EAGAIN, "busy")
        self.inflight = bytes(report)
        if self.polling:
            self.poll()

    def poll(self):
        if self.inflight is not None:
            self.taken.append(self.inflight)
            self.inflight = None

    def writable(self, timeout):
        return self.inflight is None

    def read_all(self):
        out, self.out_reports = self.out_reports, []
        return out

    def close(self):
        self.closed = True


@pytest.fixture
def gadget(tmp_path):
    sysfs = tmp_path / "sys"
    state = sysfs / "class" / "udc" / "udc0"
    state.mkdir(parents=True)
    (state / "state").write_text("configured\n")
    nodes = {fn: f"/dev/hidg{i}" for i, fn in enumerate(g.ORDER)}
    made = {}

    def open_node(path, read=False):
        made[path] = FakeNode(path, read=read)
        return made[path]

    hid = g.GadgetBackend(nodes=nodes, udc="udc0", sysfs=str(sysfs), open_node=open_node, timeout=0.01)
    hid.fake = {fn: made[path] for fn, path in nodes.items()}
    hid.state_file = state / "state"
    return hid


def test_reports_have_the_hid_layout(gadget):
    n = gadget.fake
    gadget.keyboard(0x08, [0x2C])  # Cmd+Space
    gadget.media(p.MEDIA_KEYS["volume_up"])
    gadget.acpi(p.ACPI_KEYS["wake"])
    gadget.mouse_rel(-5, 7, p.MOUSE_LEFT, 1)
    gadget.mouse_abs(4095, 2048, p.MOUSE_LEFT)
    assert n["keyboard"].taken == [bytes([0x08, 0, 0x2C, 0, 0, 0, 0, 0])]
    assert n["consumer"].taken == [bytes([1, 0, 0])]
    assert n["system"].taken == [bytes([4])]
    assert n["mouse"].taken == [bytes([1, 0xFB, 7, 1])]
    assert n["absolute"].taken == [bytes([1, 0xFF, 0x7F, 0x04, 0x40, 0])]
    assert gadget.stats["delivered"] == 5 and gadget.stats["lost_acks"] == 0
    with pytest.raises(ValueError):
        gadget.mouse_abs(4096, 0)
    with pytest.raises(ValueError):
        gadget.keyboard(0, [4, 5, 6, 7, 8, 9, 10])


def test_info_reports_enumeration_and_leds(gadget):
    i = gadget.info()
    assert i["usb_connected"] and i["usb_state"] == "configured" and not i["caps_lock"]
    gadget.fake["keyboard"].out_reports = [b"\x02"]  # the phone turned Caps Lock on
    assert gadget.info()["caps_lock"]
    gadget.state_file.write_text("suspended\n")
    assert gadget.info()["usb_connected"] is False


def test_phone_not_polling_is_a_timeout_counted_once(gadget):
    node = gadget.fake["mouse"]
    node.polling = False
    with pytest.raises(HidTimeout, match="did not take"):
        gadget.mouse_rel(10, 0)
    assert gadget.stats["lost_acks"] == 1
    with pytest.raises(HidTimeout):  # still stuck: the new report cannot even be queued
        gadget.mouse_rel(10, 0)
    assert gadget.stats["lost_acks"] == 1 and node.taken == []
    node.poll()  # the phone finally took it
    node.polling = True
    gadget.mouse_rel(0, 0)
    assert gadget.stats["late_replies"] == 1
    assert node.taken == [bytes([0, 10, 0, 0]), bytes([0, 0, 0, 0])]


def test_not_enumerated_is_not_executed(gadget):
    gadget.fake["keyboard"].error = errno.ESHUTDOWN
    gadget.state_file.write_text("not attached\n")
    with pytest.raises(HidStatusError, match="has not enumerated") as e:
        gadget.keyboard(0, [4])
    assert e.value.status == p.Status.EXEC_ERROR
    gadget.fake["keyboard"].error = errno.EBADF
    with pytest.raises(HidPortError):
        gadget.keyboard(0, [4])


def test_disconnect_does_not_count_as_delivered(gadget):
    node = gadget.fake["absolute"]
    node.polling = False
    gadget.wait_ack = False
    gadget.mouse_abs(100, 100)
    node.poll()  # the transfer completed... because the phone went away
    gadget.state_file.write_text("not attached\n")
    gadget.sync()
    assert gadget.stats["lost_acks"] == 1 and gadget.stats["delivered"] == 0


def test_pipelined_reports_are_counted_at_sync(gadget):
    gadget.wait_ack = False
    for _ in range(3):
        gadget.mouse_rel(1, 0)
    gadget.sync()
    assert gadget.stats["delivered"] == 3 and gadget.stats["lost_acks"] == 0
    gadget.fake["mouse"].polling = False
    gadget.mouse_rel(1, 0)
    gadget.sync()
    assert gadget.stats["lost_acks"] == 1


def test_missing_interface_is_bad_param_and_release_skips_it(tmp_path):
    nodes = {"keyboard": "/dev/hidg0", "absolute": "/dev/hidg1"}
    made = {}
    hid = g.GadgetBackend(nodes=nodes, udc="", open_node=lambda path, read=False: made.setdefault(path, FakeNode(path)))
    with pytest.raises(HidStatusError) as e:
        hid.mouse_rel(1, 0)
    assert e.value.status == p.Status.BAD_PARAM
    hid.release_all()
    assert made["/dev/hidg0"].taken == [bytes(8)]
    hid.close()
    assert made["/dev/hidg0"].closed


def test_no_gadget_is_a_port_error(tmp_path):
    with pytest.raises(HidPortError, match="tools/gadget.py up"):
        g.GadgetBackend(configfs=str(tmp_path), sysfs=str(tmp_path))


def test_absolute_tap_through_the_pointer_model(gadget):
    cal = PointerCalibration(screen_pt=(393.0, 852.0), mode="absolute", abs_map=(4095 / 393, 0.0, 4095 / 852, 0.0))
    cal.abs_settle = 0.0
    ptr = PointerModel(gadget, cal)
    ptr.move_to(196.5, 426.0)
    ptr.click(hold=0.0)
    reports = gadget.fake["absolute"].taken
    assert reports, "absolute reports went to the absolute interface"
    xy = {(int.from_bytes(r[1:3], "little"), int.from_bytes(r[3:5], "little")) for r in reports}
    assert xy == {(g.scale_abs(2048), g.scale_abs(2048))}
    buttons = [r[0] for r in reports]
    press = buttons.index(p.MOUSE_LEFT)
    assert buttons[press + 1:] == [0] * ptr.abs_release_repeats  # then released (repeated) at the same spot
    assert gadget.fake["mouse"].taken == []


# -- the real node class --------------------------------------------------------------------------


def test_hidg_node_over_a_fifo(tmp_path):
    path = str(tmp_path / "hidg0")
    os.mkfifo(path)
    reader = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    try:
        node = g.HidgNode(path)
        node.write(b"\x01\x02\x03\x04")
        assert os.read(reader, 16) == b"\x01\x02\x03\x04"
        assert node.writable(0.01)
        try:
            while True:  # fill the pipe: like a report the host never takes
                node.write(b"\0" * 4096)
        except BlockingIOError:
            pass
        assert not node.writable(0.02)
        try:
            while os.read(reader, 65536):  # the host takes everything
                pass
        except BlockingIOError:
            pass
        assert node.writable(0.5)
        node.close()
    finally:
        os.close(reader)


def test_gadget_tool_status_without_root(capsys, tmp_path):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    import gadget as tool

    assert tool.main(["--configfs", str(tmp_path), "status"]) == 0
    assert "not set up" in capsys.readouterr().out


def test_every_profile_fits_the_kernels_hid_limit():
    assert all(len(fns) <= g.MAX_HID_FUNCTIONS for fns in g.PROFILES.values())


def test_hid_minors_used_up_is_explained_and_cleaned_up(fs, monkeypatch):
    configfs, sysfs = fs
    real = g._mkdir

    def mkdir(path):
        if path.name == "hid.absolute":  # what the kernel answers once its 4 HID minors are taken
            raise OSError(errno.ENODEV, "No such device", str(path))
        real(path)

    monkeypatch.setattr(g, "_mkdir", mkdir)
    with pytest.raises(g.GadgetError, match="allows 4 HID gadget functions"):
        g.gadget_up("ihc", "RA", configfs=str(configfs), sysfs=str(sysfs))
    assert not (configfs / "ihc").exists()  # nothing half set up is left behind
