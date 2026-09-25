"""Video from a command that writes a stream of JPEG images to stdout (e.g. GStreamer or ffmpeg).

This is how the Orange Pi 5 Plus HDMI input is read: its capture node (rk_hdmirx) delivers raw
frames on the multi-planar V4L2 API, and the SoC's JPEG encoder (GStreamer mppjpegenc) turns them
into the same JPEG frames an MJPEG capture card sends, so the rest of the stack (streaming without
re-encoding, health checks, calibration) is unchanged. `open_pipe` plugs into V4L2Capture as its
`open_fn`: the capture thread keeps the newest frame and restarts the command with back-off when it
exits (no HDMI signal, resolution change, unplug).
"""

from __future__ import annotations

import os
import select
import shlex
import shutil
import subprocess
import time
from pathlib import Path

# Markers inside entropy-coded data that are not the end of the image: stuffed 0xFF00 and RSTn.
_NOT_END = frozenset({0x00, *range(0xD0, 0xD8)})
_STANDALONE = frozenset({0x01, *range(0xD0, 0xD8)})


def next_jpeg(buf: bytearray) -> tuple[bytes | None, int]:
    """The first complete JPEG in `buf` and how many bytes to drop, or (None, bytes to drop before
    it starts). Segments are walked by their length fields up to the scan, then the scan is read up
    to EOI, so an 0xFFD9 inside a header segment cannot end the image early."""
    start = buf.find(b"\xff\xd8")
    if start < 0:
        return None, max(0, len(buf) - 1)  # keep a trailing 0xFF: it may start the next SOI
    i, n = start + 2, len(buf)
    while True:  # header segments
        if i + 4 > n:
            return None, start
        if buf[i] != 0xFF:
            return None, start + 2  # not a JPEG after all: skip this SOI
        marker = buf[i + 1]
        if marker == 0xFF:
            i += 1
            continue
        if marker in _STANDALONE:
            i += 2
            continue
        if marker == 0xD9:
            return bytes(buf[start:i + 2]), i + 2
        length = (buf[i + 2] << 8) | buf[i + 3]
        i += 2 + length
        if marker == 0xDA:  # start of scan: entropy-coded data follows
            break
    while True:
        j = buf.find(b"\xff", i)
        if j < 0 or j + 1 >= n:
            return None, start
        if buf[j + 1] == 0xD9:
            return bytes(buf[start:j + 2]), j + 2
        if buf[j + 1] == 0xFF:
            i = j + 1
        elif buf[j + 1] in _NOT_END:
            i = j + 2
        else:  # another marker (e.g. the next scan of a progressive JPEG): keep going
            i = j + 2


class PipeCapture:
    """cv2.VideoCapture look-alike over a command's stdout: read() -> (ok, 1-D JPEG buffer)."""

    def __init__(self, argv: list[str], *, read_timeout: float = 3.0, max_frame: int = 16 << 20):
        self.argv = argv
        self.read_timeout = read_timeout
        self.max_frame = max_frame
        self._buf = bytearray()
        try:
            self.proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
        except OSError as e:
            raise OSError(f"cannot start the video command {argv[0]!r}: {e.strerror or e}") from e
        os.set_blocking(self.proc.stdout.fileno(), False)

    def isOpened(self) -> bool:  # noqa: N802 (cv2 name)
        return self.proc.poll() is None

    def read(self):
        import numpy as np  # (lazy: HID-only installs do not need numpy to import this module)

        deadline = time.monotonic() + self.read_timeout
        fd = self.proc.stdout.fileno()
        while True:
            frame, drop = next_jpeg(self._buf)
            if drop:
                del self._buf[:drop]
            if frame is not None:
                return True, np.frombuffer(frame, np.uint8)
            if len(self._buf) > self.max_frame:
                self._buf.clear()  # garbage, not JPEG: start over
            left = deadline - time.monotonic()
            if left <= 0:
                return False, None
            ready, _, _ = select.select([fd], [], [], left)
            if not ready:
                return False, None
            try:
                chunk = os.read(fd, 1 << 20)
            except BlockingIOError:
                continue
            if not chunk:  # the command exited
                return False, None
            self._buf += chunk

    def stderr_tail(self, limit: int = 400) -> str:
        if self.proc.poll() is None:
            return ""
        try:
            return self.proc.stderr.read().decode(errors="replace")[-limit:].strip()
        except (OSError, ValueError):
            return ""

    def release(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=2)
        for f in (self.proc.stdout, self.proc.stderr):
            try:
                f.close()
            except OSError:
                pass


def _gst_has(element: str) -> bool:
    if not shutil.which("gst-inspect-1.0"):
        return False
    try:
        return subprocess.run(["gst-inspect-1.0", element], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


ENCODERS = ("mpp", "gst", "ffmpeg", "builtin")


def choose_encoder(encoder: str = "auto") -> str:
    """How to turn the HDMI input into JPEG: "mpp" (the Rockchip hardware JPEG encoder, a GStreamer
    plugin of the board's image), "gst" (GStreamer's software jpegenc), "ffmpeg" (software), or
    "builtin" (ihc.video.v4l2: this package reads the driver itself and encodes with OpenCV, so it
    needs nothing installed). "auto" picks the first one available, in that order."""
    if encoder != "auto":
        if encoder not in ENCODERS:
            raise ValueError(f"unknown encoder {encoder!r} (auto, {', '.join(ENCODERS)})")
        return encoder
    if _gst_has("mppjpegenc"):
        return "mpp"
    if _gst_has("jpegenc"):
        return "gst"
    if shutil.which("ffmpeg"):
        return "ffmpeg"
    return "builtin"


def hdmi_in_command(device: str, *, fps: int = 30, quality: int = 80, encoder: str = "auto") -> list[str]:
    """A command that turns an HDMI-input capture node into a JPEG stream on stdout (for the "mpp",
    "gst" and "ffmpeg" encoders; see choose_encoder)."""
    encoder = choose_encoder(encoder)
    if encoder == "builtin":
        raise ValueError("the builtin encoder is not a command: use ihc.video.v4l2.open_reader")
    rate = f"video/x-raw,framerate={fps}/1"
    # A one-frame leaky queue: when the encoder falls behind, old frames are dropped, not delayed.
    queue = ["queue", "max-size-buffers=1", "leaky=downstream"]
    if encoder == "mpp":
        # videoconvert passes frames through untouched when the encoder takes the input's format as is,
        # and converts only when it does not (the receiver's format follows the HDMI source)
        return ["gst-launch-1.0", "-q", "v4l2src", f"device={device}", "!", "videorate", "drop-only=true", "!", rate,
                "!", *queue, "!", "videoconvert", "!", "mppjpegenc", f"quality={quality}", "!", "fdsink", "fd=1",
                "sync=false"]
    if encoder == "gst":
        return ["gst-launch-1.0", "-q", "v4l2src", f"device={device}", "!", "videorate", "drop-only=true", "!", rate,
                "!", *queue, "!", "videoconvert", "!", "jpegenc", f"quality={quality}", "!", "fdsink", "fd=1",
                "sync=false"]
    if encoder == "ffmpeg":
        q = max(2, min(31, round(31 - quality * 29 / 100)))
        return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "v4l2", "-i", device, "-r", str(fps),
                "-f", "mjpeg", "-q:v", str(q), "-"]
    raise ValueError(f"unknown encoder {encoder!r} (auto, mpp, gst or ffmpeg)")


EDIDS = ("hdmi",)


def set_edid(device: str, edid: str = "hdmi") -> str | None:
    """Advertise a 1080p60 HDMI display on the HDMI input ("hdmi", ihc.video.v4l2.edid_1080p60), so
    the phone mirrors at 1920x1080 rather than 4K: smaller JPEGs to encode and stream. Written
    straight to the driver (no v4l-utils needed). Best effort: the error text, or None once set. The
    phone sees the display reconnect."""
    if edid not in EDIDS:
        return f"unknown EDID {edid!r} (known: {', '.join(EDIDS)})"
    from .v4l2 import set_edid as write_edid

    try:
        write_edid(device)
    except OSError as e:
        return f"{device}: {e.strerror or e}"
    return None


def is_hdmi_input(name: str) -> bool:
    """V4L2 driver/card names of SoC HDMI receivers (Rockchip rk_hdmirx, mainline snps_hdmirx)."""
    return "hdmirx" in name.lower().replace("-", "").replace("_", "")


def find_hdmi_input(sysfs: str = "/sys") -> str | None:
    base = Path(sysfs) / "class" / "video4linux"
    for node in sorted(base.glob("video*")) if base.is_dir() else []:
        try:
            if is_hdmi_input((node / "name").read_text()):
                return f"/dev/{node.name}"
        except OSError:
            continue
    return None


def open_pipe(command: str | list[str], **kwargs):
    """open_fn for V4L2Capture: `command` is the argv (or a shell-quoted string)."""
    argv = shlex.split(command) if isinstance(command, str) else list(command)

    def open_fn(device, width, height, fps, fourcc, passthrough):
        return PipeCapture(argv, **kwargs)

    return open_fn
