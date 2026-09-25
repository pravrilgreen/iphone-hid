"""Video from a JPEG-stream command (the Orange Pi 5 Plus HDMI input path): splitting the byte
stream into images, the command wrapper, and the capture thread on top of it."""

import sys
import time

import cv2
import numpy as np
import pytest

from ihc.video.capture import V4L2Capture
from ihc.video.pipe import PipeCapture, find_hdmi_input, hdmi_in_command, is_hdmi_input, next_jpeg, open_pipe


def jpeg(value: int, size=(64, 48)) -> bytes:
    img = np.full((size[1], size[0], 3), value, np.uint8)
    img[5:20, 5:30] = 255 - value  # some detail, so the scan has more than one byte value
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90, cv2.IMWRITE_JPEG_RST_INTERVAL, 1])
    assert ok
    return buf.tobytes()


def split_all(data: bytes, chunk: int) -> list[bytes]:
    buf, out = bytearray(), []
    for i in range(0, len(data), chunk):
        buf += data[i:i + chunk]
        while True:
            frame, drop = next_jpeg(buf)
            del buf[:drop]
            if frame is None:
                break
            out.append(frame)
    return out


@pytest.mark.parametrize("chunk", [1, 7, 4096, 1 << 20])
def test_stream_splits_into_whole_images_whatever_the_chunking(chunk):
    frames = [jpeg(v) for v in (10, 120, 240)]
    got = split_all(b"garbage\xff" + b"".join(frames) + b"\xff\xd8partial", chunk)
    assert got == frames
    assert all(cv2.imdecode(np.frombuffer(f, np.uint8), cv2.IMREAD_COLOR) is not None for f in got)


def test_eoi_bytes_inside_a_header_segment_do_not_end_the_image():
    img = jpeg(50)
    comment = b"\xff\xfe\x00\x06\xff\xd9\xff\xd9"  # COM segment whose payload looks like EOI
    tricky = img[:2] + comment + img[2:]
    assert split_all(tricky + jpeg(60), 3) == [tricky, jpeg(60)]


WRITER = r"""
import sys, time
frames = [bytes.fromhex(h) for h in sys.argv[1:]]
for i in range(200):
    sys.stdout.buffer.write(frames[i % len(frames)])
    sys.stdout.buffer.flush()
    time.sleep(0.01)
"""


def test_pipe_capture_reads_frames_and_stops_the_command():
    frames = [jpeg(30), jpeg(200)]
    cap = PipeCapture([sys.executable, "-c", WRITER, *(f.hex() for f in frames)], read_timeout=2)
    try:
        got = [cap.read() for _ in range(4)]
        assert all(ok for ok, _ in got)
        assert [buf.tobytes() for _, buf in got] == frames * 2
    finally:
        cap.release()
    assert cap.proc.poll() is not None


def test_pipe_capture_reports_a_dead_or_silent_command():
    cap = PipeCapture([sys.executable, "-c", "print('no video', file=__import__('sys').stderr)"], read_timeout=2)
    assert cap.read() == (False, None)
    cap.proc.wait(timeout=5)
    assert "no video" in cap.stderr_tail()
    cap.release()
    silent = PipeCapture([sys.executable, "-c", "import time; time.sleep(10)"], read_timeout=0.2)
    t0 = time.monotonic()
    assert silent.read() == (False, None)
    assert time.monotonic() - t0 < 2
    silent.release()
    with pytest.raises(OSError, match="cannot start"):
        PipeCapture(["/nonexistent/encoder"])


def test_capture_thread_over_a_pipe_passes_jpeg_through():
    frames = [jpeg(80)]
    src = V4L2Capture("hdmi-in", open_fn=open_pipe([sys.executable, "-c", WRITER, frames[0].hex()], read_timeout=2),
                      max_failures=3)
    try:
        f = src.latest(timeout=5)
        assert f.jpeg == frames[0] and f.size == (64, 48)
        assert src.stats()["passthrough"] is True
    finally:
        src.close()


def test_hdmi_in_commands():
    mpp = hdmi_in_command("/dev/video0", encoder="mpp", fps=30)
    assert mpp[:2] == ["gst-launch-1.0", "-q"] and "device=/dev/video0" in mpp and "mppjpegenc" in mpp
    assert "leaky=downstream" in mpp and "fd=1" in mpp
    assert "jpegenc" in hdmi_in_command("/dev/video0", encoder="gst")
    ff = hdmi_in_command("/dev/video0", encoder="ffmpeg", quality=80)
    assert ff[0] == "ffmpeg" and ff[ff.index("-f", 5) + 1] == "mjpeg"
    with pytest.raises(ValueError):
        hdmi_in_command("/dev/video0", encoder="nope")


def test_find_hdmi_input(tmp_path):
    base = tmp_path / "class" / "video4linux"
    for node, name in (("video0", "MS2109 capture"), ("video1", "rk_hdmirx")):
        (base / node).mkdir(parents=True)
        (base / node / "name").write_text(name + "\n")
    assert find_hdmi_input(str(tmp_path)) == "/dev/video1"
    assert is_hdmi_input("snps_hdmirx") and not is_hdmi_input("uvcvideo")
    assert find_hdmi_input(str(tmp_path / "none")) is None
