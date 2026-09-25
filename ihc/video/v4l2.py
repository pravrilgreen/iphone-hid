"""Read an HDMI input straight from its V4L2 driver and hand out JPEG frames: no GStreamer, no
v4l-utils, nothing but this package's own libraries (so the self-contained box bundle works on a
board without internet).

`V4L2Reader` looks like cv2.VideoCapture to V4L2Capture (read() -> (ok, 1-D JPEG buffer)). It speaks
both V4L2 APIs, multi-planar (the Orange Pi 5 Plus rk_hdmirx) and single-planar, maps the driver's
buffers (mmap streaming), converts whatever pixel format the receiver delivers (NV12/NV21, NV16,
NV24, BGR24/RGB24, YUYV/UYVY; MJPEG passes through) and encodes JPEG with OpenCV. Frames beyond the
requested rate are returned to the driver without being encoded.

`set_edid` writes an EDID that advertises a 1080p60 HDMI display (built below from the EDID 1.3 and
CEA-861 layouts), so the phone mirrors at 1920x1080 rather than 4K.

The structures follow linux/videodev2.h for 64-bit kernels (aarch64, x86_64); the tests pin their
sizes to the kernel's ioctl numbers.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import errno
import mmap
import os
import select
import time
from typing import Callable

# -- ioctl plumbing --------------------------------------------------------------------------------

_IOC_WRITE, _IOC_READ = 1, 2


def _ioc(direction: int, nr: int, size: int) -> int:
    return (direction << 30) | (size << 16) | (ord("V") << 8) | nr


def _ior(nr, struct):
    return _ioc(_IOC_READ, nr, ctypes.sizeof(struct))


def _iow(nr, struct):
    return _ioc(_IOC_WRITE, nr, ctypes.sizeof(struct))


def _iowr(nr, struct):
    return _ioc(_IOC_READ | _IOC_WRITE, nr, ctypes.sizeof(struct))


# -- structures (linux/videodev2.h) ----------------------------------------------------------------


class Capability(ctypes.Structure):
    _fields_ = [("driver", ctypes.c_char * 16), ("card", ctypes.c_char * 32), ("bus_info", ctypes.c_char * 32),
                ("version", ctypes.c_uint32), ("capabilities", ctypes.c_uint32), ("device_caps", ctypes.c_uint32),
                ("reserved", ctypes.c_uint32 * 3)]


class PixFormat(ctypes.Structure):
    _fields_ = [("width", ctypes.c_uint32), ("height", ctypes.c_uint32), ("pixelformat", ctypes.c_uint32),
                ("field", ctypes.c_uint32), ("bytesperline", ctypes.c_uint32), ("sizeimage", ctypes.c_uint32),
                ("colorspace", ctypes.c_uint32), ("priv", ctypes.c_uint32), ("flags", ctypes.c_uint32),
                ("ycbcr_enc", ctypes.c_uint32), ("quantization", ctypes.c_uint32), ("xfer_func", ctypes.c_uint32)]


class PlanePixFormat(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("sizeimage", ctypes.c_uint32), ("bytesperline", ctypes.c_uint32), ("reserved", ctypes.c_uint16 * 6)]


class PixFormatMplane(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("width", ctypes.c_uint32), ("height", ctypes.c_uint32), ("pixelformat", ctypes.c_uint32),
                ("field", ctypes.c_uint32), ("colorspace", ctypes.c_uint32), ("plane_fmt", PlanePixFormat * 8),
                ("num_planes", ctypes.c_uint8), ("flags", ctypes.c_uint8), ("ycbcr_enc", ctypes.c_uint8),
                ("quantization", ctypes.c_uint8), ("xfer_func", ctypes.c_uint8), ("reserved", ctypes.c_uint8 * 7)]


class _FormatUnion(ctypes.Union):
    # raw_data is 200 bytes; the kernel union also holds pointers (v4l2_window), hence 8-byte alignment
    _fields_ = [("pix", PixFormat), ("pix_mp", PixFormatMplane), ("raw_data", ctypes.c_uint8 * 200),
                ("_align", ctypes.c_uint64)]


class Format(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("fmt", _FormatUnion)]


class RequestBuffers(ctypes.Structure):
    _fields_ = [("count", ctypes.c_uint32), ("type", ctypes.c_uint32), ("memory", ctypes.c_uint32),
                ("capabilities", ctypes.c_uint32), ("flags", ctypes.c_uint8), ("reserved", ctypes.c_uint8 * 3)]


class Timeval(ctypes.Structure):
    _fields_ = [("tv_sec", ctypes.c_long), ("tv_usec", ctypes.c_long)]


class Timecode(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("flags", ctypes.c_uint32), ("frames", ctypes.c_uint8),
                ("seconds", ctypes.c_uint8), ("minutes", ctypes.c_uint8), ("hours", ctypes.c_uint8),
                ("userbits", ctypes.c_uint8 * 4)]


class _PlaneM(ctypes.Union):
    _fields_ = [("mem_offset", ctypes.c_uint32), ("userptr", ctypes.c_ulong), ("fd", ctypes.c_int32)]


class Plane(ctypes.Structure):
    _fields_ = [("bytesused", ctypes.c_uint32), ("length", ctypes.c_uint32), ("m", _PlaneM),
                ("data_offset", ctypes.c_uint32), ("reserved", ctypes.c_uint32 * 11)]


class _BufferM(ctypes.Union):
    _fields_ = [("offset", ctypes.c_uint32), ("userptr", ctypes.c_ulong), ("planes", ctypes.POINTER(Plane)),
                ("fd", ctypes.c_int32)]


class Buffer(ctypes.Structure):
    _fields_ = [("index", ctypes.c_uint32), ("type", ctypes.c_uint32), ("bytesused", ctypes.c_uint32),
                ("flags", ctypes.c_uint32), ("field", ctypes.c_uint32), ("timestamp", Timeval),
                ("timecode", Timecode), ("sequence", ctypes.c_uint32), ("memory", ctypes.c_uint32),
                ("m", _BufferM), ("length", ctypes.c_uint32), ("reserved2", ctypes.c_uint32),
                ("request_fd", ctypes.c_int32)]


class Fract(ctypes.Structure):
    _fields_ = [("numerator", ctypes.c_uint32), ("denominator", ctypes.c_uint32)]


class BtTimings(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("width", ctypes.c_uint32), ("height", ctypes.c_uint32), ("interlaced", ctypes.c_uint32),
                ("polarities", ctypes.c_uint32), ("pixelclock", ctypes.c_uint64),
                ("hfrontporch", ctypes.c_uint32), ("hsync", ctypes.c_uint32), ("hbackporch", ctypes.c_uint32),
                ("vfrontporch", ctypes.c_uint32), ("vsync", ctypes.c_uint32), ("vbackporch", ctypes.c_uint32),
                ("il_vfrontporch", ctypes.c_uint32), ("il_vsync", ctypes.c_uint32), ("il_vbackporch", ctypes.c_uint32),
                ("standards", ctypes.c_uint32), ("flags", ctypes.c_uint32), ("picture_aspect", Fract),
                ("cea861_vic", ctypes.c_uint8), ("hdmi_vic", ctypes.c_uint8), ("reserved", ctypes.c_uint8 * 46)]


class _DvUnion(ctypes.Union):
    _pack_ = 1
    _fields_ = [("bt", BtTimings), ("reserved", ctypes.c_uint32 * 32)]


class DvTimings(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("type", ctypes.c_uint32), ("u", _DvUnion)]


class Edid(ctypes.Structure):
    _fields_ = [("pad", ctypes.c_uint32), ("start_block", ctypes.c_uint32), ("blocks", ctypes.c_uint32),
                ("reserved", ctypes.c_uint32 * 5), ("edid", ctypes.POINTER(ctypes.c_uint8))]


VIDIOC_QUERYCAP = _ior(0, Capability)
VIDIOC_G_FMT = _iowr(4, Format)
VIDIOC_S_FMT = _iowr(5, Format)
VIDIOC_REQBUFS = _iowr(8, RequestBuffers)
VIDIOC_QUERYBUF = _iowr(9, Buffer)
VIDIOC_QBUF = _iowr(15, Buffer)
VIDIOC_DQBUF = _iowr(17, Buffer)
VIDIOC_STREAMON = _iow(18, ctypes.c_int)
VIDIOC_STREAMOFF = _iow(19, ctypes.c_int)
VIDIOC_S_EDID = _iowr(41, Edid)
VIDIOC_S_DV_TIMINGS = _iowr(87, DvTimings)
VIDIOC_QUERY_DV_TIMINGS = _ior(99, DvTimings)

BUF_TYPE_VIDEO_CAPTURE = 1
BUF_TYPE_VIDEO_CAPTURE_MPLANE = 9
MEMORY_MMAP = 1
CAP_VIDEO_CAPTURE = 0x00000001
CAP_VIDEO_CAPTURE_MPLANE = 0x00001000
CAP_STREAMING = 0x04000000
CAP_DEVICE_CAPS = 0x80000000


def fourcc(code: str) -> int:
    return int.from_bytes(code.encode(), "little")


def fourcc_str(value: int) -> str:
    return value.to_bytes(4, "little").decode(errors="replace")


# pixel formats we can turn into JPEG, most preferred first (NV12 is the smallest to move around)
PREFERRED = ("NV12", "NV16", "NV24", "BGR3", "RGB3", "NV21", "YUYV", "UYVY", "MJPG")
# the same layouts with each plane in its own buffer
MULTI_BUFFER = {"NM12": "NV12", "NM21": "NV21", "NM16": "NV16"}


# -- conversion ------------------------------------------------------------------------------------


def _plane_rows(buf, offset: int, rows: int, bpl: int, width_bytes: int):
    import numpy as np

    a = np.frombuffer(buf, np.uint8, count=rows * bpl, offset=offset)
    return a.reshape(rows, bpl)[:, :width_bytes]


def to_bgr(fmt: str, width: int, height: int, planes: list[tuple[memoryview | bytes, int]]):
    """A BGR image from one frame. `planes`: (data, bytesperline) per V4L2 plane; single-buffer
    semi-planar formats keep their chroma right after the luma. YCbCr is taken as video range
    (BT.601), what HDMI receivers deliver."""
    import cv2
    import numpy as np

    data0, bpl0 = planes[0]
    if fmt in ("BGR3", "RGB3"):
        img = _plane_rows(data0, 0, height, bpl0, width * 3).reshape(height, width, 3)
        return np.ascontiguousarray(img if fmt == "BGR3" else img[:, :, ::-1])
    if fmt in ("YUYV", "UYVY"):
        packed = _plane_rows(data0, 0, height, bpl0, width * 2).reshape(height, width, 2)
        return cv2.cvtColor(np.ascontiguousarray(packed), cv2.COLOR_YUV2BGR_YUYV if fmt == "YUYV" else cv2.COLOR_YUV2BGR_UYVY)
    chroma_rows = height // 2 if fmt in ("NV12", "NV21") else height
    chroma_bytes = width * 2 if fmt == "NV24" else width
    y = _plane_rows(data0, 0, height, bpl0, width)
    if len(planes) > 1:
        data1, bpl1 = planes[1]
        uv = _plane_rows(data1, 0, chroma_rows, bpl1, chroma_bytes)
    else:
        uv = _plane_rows(data0, height * bpl0, chroma_rows, bpl0 * (2 if fmt == "NV24" else 1), chroma_bytes)
    if fmt in ("NV12", "NV21"):
        return cv2.cvtColor(np.vstack([y, uv]), cv2.COLOR_YUV2BGR_NV12 if fmt == "NV12" else cv2.COLOR_YUV2BGR_NV21)
    if fmt == "NV16":  # 4:2:2: each pixel pairs its luma with alternately U and V, which is YUYV order
        return cv2.cvtColor(np.ascontiguousarray(np.dstack([y, uv])), cv2.COLOR_YUV2BGR_YUYV)
    if fmt == "NV24":  # 4:4:4: a U and a V byte per pixel; JPEG keeps 4:2:0 anyway, so drop to NV12
        uv420 = np.empty((height // 2, width), np.uint8)
        uv420[:, 0::2], uv420[:, 1::2] = uv[0::2, 0::4], uv[0::2, 1::4]
        return cv2.cvtColor(np.vstack([y, uv420]), cv2.COLOR_YUV2BGR_NV12)
    raise ValueError(f"unsupported pixel format {fmt}")


# -- EDID ------------------------------------------------------------------------------------------

# 1920x1080 at 60 Hz, 148.5 MHz (CEA-861 VIC 16), +hsync +vsync, 527 x 296 mm
DTD_1080P60 = bytes([0x02, 0x3A, 0x80, 0x18, 0x71, 0x38, 0x2D, 0x40, 0x58, 0x2C, 0x45, 0x00,
                     0x0F, 0x28, 0x21, 0x00, 0x00, 0x1E])


def _checksummed(block: bytearray) -> bytes:
    block[127] = (-sum(block[:127])) & 0xFF
    return bytes(block)


def edid_1080p60(name: str = "iphone-hid") -> bytes:
    """A 256-byte EDID (EDID 1.3 base block + CEA-861 extension) for an HDMI display whose preferred
    and only video mode is 1920x1080 at 60 Hz, with 2-channel LPCM audio."""
    base = bytearray(128)
    base[0:8] = b"\x00\xff\xff\xff\xff\xff\xff\x00"
    base[8:10] = (((ord("I") - 64) << 10) | ((ord("H") - 64) << 5) | (ord("C") - 64)).to_bytes(2, "big")
    base[10:12] = (1).to_bytes(2, "little")  # product code
    base[16], base[17] = 1, 2026 - 1990  # week, year of manufacture
    base[18], base[19] = 1, 3  # EDID 1.3
    base[20] = 0x80  # digital input
    base[21], base[22] = 53, 30  # image size in cm (16:9)
    base[23] = 120  # gamma 2.2
    base[24] = 0x0E  # RGB colour, sRGB default colour space; the first detailed timing is the preferred mode
    base[25:35] = bytes([0xEE, 0x91, 0xA3, 0x54, 0x4C, 0x99, 0x26, 0x0F, 0x50, 0x54])  # sRGB chromaticity
    base[35:38] = bytes([0x20, 0x00, 0x00])  # established timing: 640x480 at 60 Hz (required for HDMI)
    base[38:54] = b"\x01\x01" * 8  # no standard timings
    base[54:72] = DTD_1080P60
    base[72:90] = bytes([0, 0, 0, 0xFD, 0, 56, 75, 30, 83, 15, 0, 0x0A]) + b"\x20" * 6  # range limits
    label = name.encode("ascii", "replace")[:12] + b"\x0a"
    base[90:108] = bytes([0, 0, 0, 0xFC, 0]) + label.ljust(13, b"\x20")  # monitor name
    base[108:126] = bytes([0, 0, 0, 0x10, 0]) + bytes(13)  # dummy descriptor
    base[126] = 1  # one extension block
    ext = bytearray(128)
    blocks = bytes([0x23, 0x09, 0x04, 0x07,  # audio: LPCM, 2 channels, 48 kHz, 16/20/24 bit
                    0x41, 0x10,  # video: VIC 16 (1080p60); the detailed timing below is the native one
                    0x65, 0x03, 0x0C, 0x00, 0x10, 0x00,  # HDMI vendor block, physical address 1.0.0.0
                    0xE2, 0x00, 0x4A])  # video capability: RGB range selectable, IT and CE underscanned
    ext[0], ext[1] = 0x02, 0x03  # CEA-861 extension, revision 3
    ext[2] = 4 + len(blocks)  # detailed timings start here
    ext[3] = 0xC1  # underscan, basic audio; one native detailed timing
    ext[4:4 + len(blocks)] = blocks
    ext[ext[2]:ext[2] + 18] = DTD_1080P60
    return _checksummed(base) + _checksummed(ext)


# -- the reader -------------------------------------------------------------------------------------


def _libc_ioctl():
    libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
    fn = libc.ioctl
    fn.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_void_p]
    fn.restype = ctypes.c_int

    def ioctl(fd: int, request: int, arg) -> None:
        ptr = ctypes.cast(ctypes.pointer(arg), ctypes.c_void_p) if not isinstance(arg, int) else arg
        while fn(fd, request, ptr) < 0:
            err = ctypes.get_errno()
            if err != errno.EINTR:
                raise OSError(err, os.strerror(err))

    return ioctl


def set_edid(device: str, edid: bytes | None = None, *, ioctl=None) -> None:
    """Write `edid` (default: edid_1080p60()) to the HDMI input; the source sees the display replug."""
    data = edid or edid_1080p60()
    ioctl = ioctl or _libc_ioctl()
    buf = (ctypes.c_uint8 * len(data)).from_buffer_copy(data)
    req = Edid(pad=0, start_block=0, blocks=len(data) // 128, edid=ctypes.cast(buf, ctypes.POINTER(ctypes.c_uint8)))
    fd = os.open(device, os.O_RDWR)
    try:
        ioctl(fd, VIDIOC_S_EDID, req)
    finally:
        os.close(fd)


NO_SIGNAL = {
    errno.ENOLINK: "no signal: nothing plugged in, or the source sends no picture",
    errno.ENOLCK: "signal not stable yet",
    errno.ERANGE: "signal out of the receiver's range",
}


def describe_signal(device: str, *, ioctl=None) -> str:
    """One line on what an HDMI input receives right now, straight from the driver (for a board
    without v4l-utils)."""
    ioctl = ioctl or _libc_ioctl()
    fd = os.open(device, os.O_RDWR | os.O_NONBLOCK)
    try:
        dv = DvTimings()
        try:
            ioctl(fd, VIDIOC_QUERY_DV_TIMINGS, dv)
        except OSError as e:
            return NO_SIGNAL.get(e.errno, f"no DV timings ({e.strerror})")
    finally:
        os.close(fd)
    bt = dv.u.bt
    total = (bt.width + bt.hfrontporch + bt.hsync + bt.hbackporch) * \
        (bt.height + bt.vfrontporch + bt.vsync + bt.vbackporch)
    rate = f"{bt.pixelclock / total:.2f}" if total and bt.pixelclock else "?"
    return f"{bt.width}x{bt.height}{'i' if bt.interlaced else 'p'}{rate}, pixel clock {bt.pixelclock / 1e6:.2f} MHz"


class V4L2Reader:
    """cv2.VideoCapture look-alike over a V4L2 capture node: read() -> (ok, 1-D JPEG buffer)."""

    def __init__(self, device: str, *, fps: int = 30, quality: int = 80, buffers: int = 4, timeout: float = 2.0,
                 ioctl: Callable | None = None, map_buffer: Callable | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.device = device
        self.quality = quality
        self.timeout = timeout
        self.min_interval = 1.0 / fps if fps else 0.0
        self._ioctl = ioctl or _libc_ioctl()
        self._map = map_buffer or (lambda fd, length, offset: mmap.mmap(fd, length, mmap.MAP_SHARED,
                                                                        mmap.PROT_READ | mmap.PROT_WRITE,
                                                                        offset=offset))
        self._clock = clock
        self._last = -1e9
        self._maps: list[list] = []
        self._streaming = False
        self.fd = os.open(device, os.O_RDWR | os.O_NONBLOCK)
        try:
            self._start(buffers)
        except BaseException:
            self.release()
            raise

    # -- set-up ------------------------------------------------------------------------------------

    def _start(self, count: int) -> None:
        cap = Capability()
        self._ioctl(self.fd, VIDIOC_QUERYCAP, cap)
        caps = cap.device_caps if cap.capabilities & CAP_DEVICE_CAPS else cap.capabilities
        if caps & CAP_VIDEO_CAPTURE_MPLANE:
            self.type = BUF_TYPE_VIDEO_CAPTURE_MPLANE
        elif caps & CAP_VIDEO_CAPTURE:
            self.type = BUF_TYPE_VIDEO_CAPTURE
        else:
            raise OSError(errno.ENODEV, f"{self.device} is not a video capture device")
        if not caps & CAP_STREAMING:
            raise OSError(errno.ENODEV, f"{self.device} does not support streaming I/O")
        self.driver = cap.driver.decode(errors="replace")
        self.timings = self._lock_timings()
        self._choose_format()
        req = RequestBuffers(count=count, type=self.type, memory=MEMORY_MMAP)
        self._ioctl(self.fd, VIDIOC_REQBUFS, req)
        if req.count < 2:
            raise OSError(errno.ENOMEM, f"{self.device}: the driver gave {req.count} buffer(s)")
        for index in range(req.count):
            buf, planes = self._buffer(index)
            self._ioctl(self.fd, VIDIOC_QUERYBUF, buf)
            if self.mplane:
                maps = [self._map(self.fd, planes[p].length, planes[p].m.mem_offset) for p in range(buf.length)]
            else:
                maps = [self._map(self.fd, buf.length, buf.m.offset)]
            self._maps.append(maps)
            self._ioctl(self.fd, VIDIOC_QBUF, buf)
        self._ioctl(self.fd, VIDIOC_STREAMON, ctypes.c_int(self.type))
        self._streaming = True

    @property
    def mplane(self) -> bool:
        return self.type == BUF_TYPE_VIDEO_CAPTURE_MPLANE

    def _lock_timings(self) -> tuple[int, int] | None:
        """An HDMI receiver streams the timings it detects once they are set; no signal is an error
        (the capture thread retries with back-off). Nodes without DV timings (cameras, USB capture
        cards) skip this."""
        dv = DvTimings()
        try:
            self._ioctl(self.fd, VIDIOC_QUERY_DV_TIMINGS, dv)
        except OSError as e:
            if e.errno in (errno.ENOLINK, errno.ENOLCK, errno.ERANGE):
                raise OSError(e.errno, f"{self.device}: no usable HDMI signal ({e.strerror})") from None
            return None  # ENOTTY / ENODATA / EINVAL: not a DV device, or nothing to lock
        try:
            self._ioctl(self.fd, VIDIOC_S_DV_TIMINGS, dv)
        except OSError:
            pass  # some drivers follow the source by themselves
        return dv.u.bt.width, dv.u.bt.height

    def _choose_format(self) -> None:
        fmt = Format(type=self.type)
        self._ioctl(self.fd, VIDIOC_G_FMT, fmt)
        current = fourcc_str(fmt.fmt.pix_mp.pixelformat if self.mplane else fmt.fmt.pix.pixelformat)
        if current != PREFERRED[0]:
            want = Format(type=self.type)
            ctypes.memmove(ctypes.addressof(want), ctypes.addressof(fmt), ctypes.sizeof(fmt))
            if self.mplane:
                want.fmt.pix_mp.pixelformat = fourcc(PREFERRED[0])
            else:
                want.fmt.pix.pixelformat = fourcc(PREFERRED[0])
            try:
                self._ioctl(self.fd, VIDIOC_S_FMT, want)
            except OSError:
                pass  # the receiver may only offer its own format: keep it
            self._ioctl(self.fd, VIDIOC_G_FMT, fmt)
        if self.mplane:
            p = fmt.fmt.pix_mp
            self.pixfmt = fourcc_str(p.pixelformat)
            self.width, self.height = p.width, p.height
            self.bytesperline = [p.plane_fmt[i].bytesperline for i in range(max(1, p.num_planes))]
        else:
            p = fmt.fmt.pix
            self.pixfmt = fourcc_str(p.pixelformat)
            self.width, self.height = p.width, p.height
            self.bytesperline = [p.bytesperline]
        if self.pixfmt not in PREFERRED and self.pixfmt not in MULTI_BUFFER:
            raise OSError(errno.EINVAL, f"{self.device} delivers {self.pixfmt}, which this reader cannot convert")

    def _buffer(self, index: int = 0) -> tuple[Buffer, ctypes.Array | None]:
        buf = Buffer(index=index, type=self.type, memory=MEMORY_MMAP)
        planes = None
        if self.mplane:
            planes = (Plane * 8)()
            buf.m.planes = ctypes.cast(planes, ctypes.POINTER(Plane))
            buf.length = 8
        return buf, planes

    # -- frames ------------------------------------------------------------------------------------

    def isOpened(self) -> bool:  # noqa: N802 (cv2 name)
        return self._streaming

    def read(self):
        import cv2
        import numpy as np

        deadline = self._clock() + self.timeout
        while True:
            left = deadline - self._clock()
            if left <= 0:
                return False, None
            ready, _, _ = select.select([self.fd], [], [], left)
            if not ready:
                return False, None
            buf, planes = self._buffer()
            try:
                self._ioctl(self.fd, VIDIOC_DQBUF, buf)
            except BlockingIOError:
                continue
            except OSError as e:
                if e.errno == errno.EAGAIN:
                    continue
                raise  # signal lost or format changed: the capture thread reopens
            try:
                now = self._clock()
                if now - self._last < self.min_interval * 0.9:
                    continue  # faster than wanted: give the buffer straight back
                self._last = now
                maps = self._maps[buf.index]
                if self.mplane:
                    used = [(maps[p], planes[p].data_offset, planes[p].bytesused) for p in range(buf.length)]
                else:
                    used = [(maps[0], 0, buf.bytesused)]
                if self.pixfmt == "MJPG":
                    m, off, n = used[0]
                    return True, np.frombuffer(bytes(m[off:off + n]), np.uint8)
                bpls = (self.bytesperline + self.bytesperline[-1:] * len(used))[:len(used)]
                views = [(memoryview(m)[off:off + n] if n else memoryview(m)[off:], bpl)
                         for (m, off, n), bpl in zip(used, bpls)]
                try:
                    img = to_bgr(MULTI_BUFFER.get(self.pixfmt, self.pixfmt), self.width, self.height, views)
                except ValueError:
                    return False, None  # a short or damaged frame: the capture thread counts a failure
                finally:
                    del views
                ok, jpeg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, self.quality])
                if ok:
                    return True, jpeg.reshape(-1)
            finally:
                self._ioctl(self.fd, VIDIOC_QBUF, buf)

    def release(self) -> None:
        if self._streaming:
            try:
                self._ioctl(self.fd, VIDIOC_STREAMOFF, ctypes.c_int(self.type))
            except OSError:
                pass
            self._streaming = False
        for maps in self._maps:
            for m in maps:
                try:
                    m.close()
                except (BufferError, ValueError):
                    pass
        self._maps = []
        if self.fd >= 0:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = -1


def open_reader(fps: int = 30, quality: int = 80):
    """open_fn for V4L2Capture."""

    def open_fn(device, width, height, fps_req, fourcc_req, passthrough):
        return V4L2Reader(str(device), fps=fps, quality=quality)

    return open_fn
