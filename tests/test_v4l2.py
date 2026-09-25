"""The built-in V4L2 reader and EDID (the offline HDMI-input path): structure layouts pinned to the
kernel's ioctl numbers, pixel format conversions, and the streaming loop against a fake driver."""

import ctypes
import errno
import shutil
import subprocess

import cv2
import numpy as np
import pytest

from ihc.video import v4l2 as v
from ihc.video.capture import V4L2Capture

# -- layouts ---------------------------------------------------------------------------------------


def test_ioctl_numbers_match_the_kernel():
    """Each number encodes its structure's size, so these pin the layouts to linux/videodev2.h
    (64-bit)."""
    assert v.VIDIOC_QUERYCAP == 0x80685600
    assert v.VIDIOC_G_FMT == 0xC0D05604 and v.VIDIOC_S_FMT == 0xC0D05605
    assert v.VIDIOC_REQBUFS == 0xC0145608
    assert v.VIDIOC_QUERYBUF == 0xC0585609 and v.VIDIOC_QBUF == 0xC058560F and v.VIDIOC_DQBUF == 0xC0585611
    assert v.VIDIOC_STREAMON == 0x40045612 and v.VIDIOC_STREAMOFF == 0x40045613
    assert v.VIDIOC_S_EDID == 0xC0285629
    assert v.VIDIOC_QUERY_DV_TIMINGS == 0x80845663 and v.VIDIOC_S_DV_TIMINGS == 0xC0845657
    assert ctypes.sizeof(v.PixFormatMplane) == 192 and ctypes.sizeof(v.Plane) == 64


# -- EDID ------------------------------------------------------------------------------------------


def test_edid_advertises_1080p60():
    e = v.edid_1080p60()
    assert len(e) == 256 and sum(e[:128]) % 256 == 0 and sum(e[128:]) % 256 == 0
    assert e[:8] == b"\x00\xff\xff\xff\xff\xff\xff\x00" and e[126] == 1
    dtd = e[54:72]
    assert int.from_bytes(dtd[0:2], "little") == 14850  # 148.50 MHz
    assert dtd[2] | (dtd[4] >> 4) << 8 == 1920 and dtd[5] | (dtd[7] >> 4) << 8 == 1080
    ext = e[128:]
    assert ext[0] == 0x02 and bytes([0x41, 0x10]) in ext[4:ext[2]]  # video data block: VIC 16
    assert bytes([0x03, 0x0C, 0x00]) in ext[4:ext[2]]  # HDMI vendor block


@pytest.mark.skipif(not shutil.which("edid-decode"), reason="edid-decode not installed")
def test_edid_passes_edid_decode(tmp_path):
    path = tmp_path / "edid.bin"
    path.write_bytes(v.edid_1080p60())
    out = subprocess.run(["edid-decode", "--check", str(path)], capture_output=True, text=True).stdout
    assert "EDID conformity: PASS" in out and "1920x1080   60.000000 Hz" in out


def test_set_edid_hands_the_blocks_to_the_driver(tmp_path):
    node = tmp_path / "video0"
    node.write_bytes(b"")
    seen = {}

    def ioctl(fd, req, arg):
        seen["req"], seen["blocks"] = req, arg.blocks
        seen["data"] = ctypes.string_at(arg.edid, arg.blocks * 128)

    v.set_edid(str(node), ioctl=ioctl)
    assert seen == {"req": v.VIDIOC_S_EDID, "blocks": 2, "data": v.edid_1080p60()}


# -- conversions -----------------------------------------------------------------------------------


def source(w=64, h=32):
    x, y = np.meshgrid(np.linspace(0, 1, w), np.linspace(0, 1, h))
    return np.dstack([60 + 120 * x, 40 + 150 * y, 200 - 120 * x * y]).astype(np.uint8)


def yuv_planes(bgr):
    """Video-range Y, U, V at full resolution (the chroma of 4:2:0, repeated), as a receiver sends."""
    h, w = bgr.shape[:2]
    i420 = cv2.cvtColor(bgr, cv2.COLOR_BGR2YUV_I420).reshape(-1)
    y = i420[:h * w].reshape(h, w)
    u = i420[h * w:h * w + h * w // 4].reshape(h // 2, w // 2)
    vv = i420[h * w + h * w // 4:].reshape(h // 2, w // 2)
    up = lambda c: np.repeat(np.repeat(c, 2, axis=0), 2, axis=1)  # noqa: E731
    return y, up(u), up(vv)


def padded(rows, bpl):
    out = np.full((rows.shape[0], bpl), 0xAB, np.uint8)  # garbage in the stride padding
    out[:, :rows.shape[1]] = rows
    return out


def encode(fmt, bgr, pad=16):
    """The frame as a driver would deliver it: (planes, width, height)."""
    h, w = bgr.shape[:2]
    y, u, vv = yuv_planes(bgr)
    if fmt == "BGR3":
        return [(padded(bgr.reshape(h, w * 3), w * 3 + pad).tobytes(), w * 3 + pad)]
    if fmt == "YUYV":
        packed = np.empty((h, w, 2), np.uint8)
        packed[:, :, 0] = y
        packed[:, 0::2, 1], packed[:, 1::2, 1] = u[:, 0::2], vv[:, 0::2]
        return [(padded(packed.reshape(h, 2 * w), 2 * w + pad).tobytes(), 2 * w + pad)]
    if fmt == "NV12":
        uv = np.empty((h // 2, w), np.uint8)
        uv[:, 0::2], uv[:, 1::2] = u[0::2, 0::2], vv[0::2, 0::2]
    elif fmt == "NV16":
        uv = np.empty((h, w), np.uint8)
        uv[:, 0::2], uv[:, 1::2] = u[:, 0::2], vv[:, 0::2]
    elif fmt == "NV24":
        uv = np.empty((h, 2 * w), np.uint8)
        uv[:, 0::2], uv[:, 1::2] = u, vv
        bpl = w + pad
        return [(padded(y, bpl).tobytes() + padded(uv, 2 * bpl).tobytes(), bpl)]
    bpl = w + pad
    return [(padded(y, bpl).tobytes() + padded(uv, bpl).tobytes(), bpl)]


@pytest.mark.parametrize("fmt", ["BGR3", "NV12", "NV16", "NV24", "YUYV"])
def test_formats_convert_back_to_the_picture(fmt):
    bgr = source()
    out = v.to_bgr(fmt, 64, 32, encode(fmt, bgr))
    assert out.shape == bgr.shape
    assert np.abs(out.astype(int) - bgr.astype(int)).mean() < 6


def test_two_buffer_nv12():
    bgr = source()
    (data, bpl), = encode("NV12", bgr)
    y, uv = data[:32 * bpl], data[32 * bpl:]
    out = v.to_bgr("NV12", 64, 32, [(y, bpl), (uv, bpl)])
    assert np.abs(out.astype(int) - bgr.astype(int)).mean() < 6


# -- the streaming loop against a fake driver ------------------------------------------------------


class FakeMap(bytearray):
    closed = False

    def close(self):
        self.closed = True


class FakeDriver:
    """A capture node: multi-planar NV12 with DV timings (like rk_hdmirx), or single-planar."""

    def __init__(self, fmt="NV12", *, mplane=True, dv=True, signal=True, w=64, h=32):
        self.fmt, self.mplane, self.dv, self.signal = fmt, mplane, dv, signal
        self.w, self.h = w, h
        self.frame = encode(fmt, source(w, h), pad=0)[0][0] if fmt != "MJPG" else \
            cv2.imencode(".jpg", source(w, h))[1].tobytes()
        self.bpl = {"NV12": w, "YUYV": 2 * w, "BGR3": 3 * w, "MJPG": 0}[fmt]
        self.queued, self.maps, self.calls = [], {}, []

    def ioctl(self, fd, req, arg):
        self.calls.append(req)
        if req == v.VIDIOC_QUERYCAP:
            arg.driver = b"fake_hdmirx"
            arg.capabilities = v.CAP_DEVICE_CAPS | v.CAP_STREAMING | \
                (v.CAP_VIDEO_CAPTURE_MPLANE if self.mplane else v.CAP_VIDEO_CAPTURE)
            arg.device_caps = arg.capabilities & ~v.CAP_DEVICE_CAPS
        elif req == v.VIDIOC_QUERY_DV_TIMINGS:
            if not self.dv:
                raise OSError(errno.ENOTTY, "no DV timings")
            if not self.signal:
                raise OSError(errno.ENOLINK, "Link has been severed")
            arg.u.bt.width, arg.u.bt.height = 1920, 1080
        elif req in (v.VIDIOC_G_FMT, v.VIDIOC_S_FMT):
            if req == v.VIDIOC_S_FMT:
                raise OSError(errno.EINVAL, "fixed format")  # like a receiver that keeps its own format
            if self.mplane:
                p = arg.fmt.pix_mp
                p.width, p.height, p.pixelformat, p.num_planes = self.w, self.h, v.fourcc(self.fmt), 1
                p.plane_fmt[0].bytesperline, p.plane_fmt[0].sizeimage = self.bpl, len(self.frame)
            else:
                p = arg.fmt.pix
                p.width, p.height, p.pixelformat = self.w, self.h, v.fourcc(self.fmt)
                p.bytesperline, p.sizeimage = self.bpl, len(self.frame)
        elif req == v.VIDIOC_REQBUFS:
            arg.count = min(arg.count, 3)
        elif req == v.VIDIOC_QUERYBUF:
            if self.mplane:
                arg.length = 1
                arg.m.planes[0].length, arg.m.planes[0].m.mem_offset = len(self.frame), arg.index * 4096
            else:
                arg.length, arg.m.offset = len(self.frame), arg.index * 4096
        elif req == v.VIDIOC_QBUF:
            self.queued.append(arg.index)
        elif req == v.VIDIOC_DQBUF:
            if not self.queued:
                raise OSError(errno.EAGAIN, "try again")
            arg.index = self.queued.pop(0)
            self.maps[arg.index * 4096][:len(self.frame)] = self.frame
            if self.mplane:
                arg.length = 1
                arg.m.planes[0].bytesused, arg.m.planes[0].data_offset = len(self.frame), 0
            else:
                arg.bytesused = len(self.frame)

    def map_buffer(self, fd, length, offset):
        self.maps[offset] = FakeMap(length)
        return self.maps[offset]


@pytest.fixture
def node(tmp_path):
    path = tmp_path / "video0"
    path.write_bytes(b"")  # a regular file: always ready for select()
    return str(path)


def reader(node, driver, **kw):
    return v.V4L2Reader(node, ioctl=driver.ioctl, map_buffer=driver.map_buffer, **kw)


@pytest.mark.parametrize("fmt,mplane", [("NV12", True), ("YUYV", False), ("BGR3", True)])
def test_reader_streams_jpeg_frames(node, fmt, mplane):
    drv = FakeDriver(fmt, mplane=mplane, dv=mplane)
    r = reader(node, drv, fps=0)
    try:
        assert r.pixfmt == fmt and (r.width, r.height) == (64, 32)
        for _ in range(5):  # more frames than buffers: each goes back to the driver
            ok, jpeg = r.read()
            assert ok
            img = cv2.imdecode(jpeg, cv2.IMREAD_COLOR)
            assert img.shape == (32, 64, 3) and np.abs(img.astype(int) - source().astype(int)).mean() < 8
        assert r.timings == ((1920, 1080) if mplane else None)
    finally:
        r.release()
    assert v.VIDIOC_STREAMOFF in drv.calls and all(m.closed for m in drv.maps.values())


def test_mjpeg_passes_through(node):
    drv = FakeDriver("MJPG", mplane=False, dv=False)
    r = reader(node, drv, fps=0)
    try:
        ok, jpeg = r.read()
        assert ok and jpeg.tobytes() == drv.frame
    finally:
        r.release()


def test_no_signal_is_an_error_to_retry(node):
    with pytest.raises(OSError, match="no usable HDMI signal"):
        reader(node, FakeDriver(signal=False))


def test_describe_signal(node):
    def ioctl(fd, req, arg):  # CTA-861 1080p60: 2200 x 1125 total at 148.5 MHz
        assert req == v.VIDIOC_QUERY_DV_TIMINGS
        bt = arg.u.bt
        bt.width, bt.height, bt.pixelclock = 1920, 1080, 148_500_000
        bt.hfrontporch, bt.hsync, bt.hbackporch = 88, 44, 148
        bt.vfrontporch, bt.vsync, bt.vbackporch = 4, 5, 36

    assert v.describe_signal(node, ioctl=ioctl) == "1920x1080p60.00, pixel clock 148.50 MHz"
    assert v.describe_signal(node, ioctl=FakeDriver(signal=False).ioctl).startswith("no signal")


def test_frames_beyond_the_rate_are_not_encoded(node, monkeypatch):
    drv = FakeDriver()
    t = [100.0]

    def clock():  # 10 ms pass on every look at the clock
        t[0] += 0.01
        return t[0]

    encoded = []
    real = cv2.imencode
    monkeypatch.setattr(cv2, "imencode", lambda *a, **k: encoded.append(1) or real(*a, **k))
    r = reader(node, drv, fps=10, clock=clock)
    try:
        for _ in range(3):
            assert r.read()[0]
        assert len(encoded) == 3
        assert drv.calls.count(v.VIDIOC_DQBUF) > 3  # the frames in between went back unencoded
    finally:
        r.release()


def test_capture_thread_runs_on_the_reader(node):
    drv = FakeDriver()
    src = V4L2Capture(node, open_fn=lambda *a: reader(node, drv, fps=0), max_failures=3)
    try:
        f = src.latest(timeout=5)
        assert f.size == (64, 32) and f.jpeg[:2] == b"\xff\xd8"
    finally:
        src.close()
