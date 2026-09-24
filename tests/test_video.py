import threading
import time

import cv2
import numpy as np
import pytest

from ihc.video.capture import V4L2Capture, device_index, frame_from_buffer, list_video_devices
from ihc.video.frame import Frame, StaticSource, encode_jpeg, jpeg_size
from ihc.video.geometry import ScreenRect, fit_screen_rect


def jpeg_buffer(w=64, h=48, value=128):
    img = np.full((h, w, 3), value, np.uint8)
    return np.frombuffer(encode_jpeg(img), np.uint8).reshape(1, -1)


class FakeCap:
    """Stand-in for cv2.VideoCapture: yields scripted results, then keeps failing."""

    def __init__(self, script):
        self.script = list(script)
        self.released = False

    def read(self):
        time.sleep(0.002)
        if self.script:
            item = self.script.pop(0)
            return (item is not None), item
        return False, None

    def release(self):
        self.released = True


def test_jpeg_passthrough_keeps_card_bytes():
    buf = jpeg_buffer()
    f = frame_from_buffer(buf, 7, 1.0)
    assert f.jpeg == buf.tobytes() and (f.width, f.height) == (64, 48) and not f.decoded
    assert f.image.shape == (48, 64, 3) and f.to_jpeg() is f.jpeg


def test_decoded_buffers_are_accepted_and_garbage_rejected():
    f = frame_from_buffer(np.zeros((10, 20, 3), np.uint8), 0, 0.0)
    assert f.jpeg is None and f.size == (20, 10)
    with pytest.raises(ValueError):
        frame_from_buffer(np.zeros((1, 10), np.uint8), 0, 0.0)


def test_capture_thread_serves_latest_and_reopens():
    opened = []

    def open_fn(device, *args):
        if len(opened) == 1:
            opened.append("fail")
            raise OSError("unplugged")
        cap = FakeCap([jpeg_buffer(value=40 * (len(opened) + 1))] * 5)
        opened.append(cap)
        return cap

    cap = V4L2Capture("/dev/video9", open_fn=open_fn, max_failures=3, backoff=(0.01, 0.02))
    try:
        f = cap.latest(timeout=2)
        assert f.jpeg is not None
        f2 = cap.latest(newer_than=f.seq, timeout=2)
        assert f2.seq > f.seq
        deadline = time.monotonic() + 3
        while cap.reopens < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert cap.reopens >= 2 and len(opened) >= 3  # lost frames -> reopen -> open error -> reopen
        s = cap.stats()
        assert s["passthrough"] and s["frames"] >= 5
    finally:
        cap.close()


def test_capture_thread_survives_a_driver_exception():
    class Exploding:
        def __init__(self):
            self.n = 0

        def read(self):
            self.n += 1
            if self.n == 3:
                raise cv2.error("device vanished")
            return True, jpeg_buffer()

        def release(self):
            pass

    caps = []

    def open_fn(*args):
        caps.append(Exploding())
        return caps[-1]

    cap = V4L2Capture("/dev/video9", open_fn=open_fn, backoff=(0.01, 0.02))
    try:
        deadline = time.monotonic() + 3
        while len(caps) < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(caps) >= 3 and cap.reopens >= 2  # reopened each time, the thread lives on
        assert cap._thread.is_alive()
    finally:
        cap.close()


def test_capture_timeout_reports_status():
    def open_fn(*args):
        raise OSError("no such device")

    cap = V4L2Capture("/dev/video9", open_fn=open_fn, backoff=(0.01, 0.01))
    try:
        with pytest.raises(TimeoutError, match="no such device"):
            cap.latest(timeout=0.1)
    finally:
        cap.close()


def test_fit_screen_rect_matches_mirroring():
    r = fit_screen_rect((1920, 1080), (393, 852))
    assert (r.w, r.h) == (498, 1080) and r.as_int_box() == (711, 0, 1209, 1080)
    land = fit_screen_rect((1920, 1080), (393, 852), landscape=True)
    assert land.w == 1920 and land.orientation == "landscape"
    nx, ny = r.frame_to_norm(*r.norm_to_frame(0.25, 0.75))
    assert (round(nx, 6), round(ny, 6)) == (0.25, 0.75)


def test_jpeg_size_and_static_source():
    data = jpeg_buffer(w=33, h=21).tobytes()
    assert jpeg_size(data) == (33, 21)
    src = StaticSource([Frame.from_jpeg(data)])
    a, b = src.latest(), src.latest()
    assert b.seq == a.seq + 1 and src.size == (33, 21)


def test_list_video_devices_does_not_crash():
    assert isinstance(list_video_devices(), list)


def test_device_index_resolves_links(tmp_path):
    link = tmp_path / "usb-0:1.3:1.0-video-index0"
    link.symlink_to("/dev/video7")
    assert device_index(str(link)) == 7 and device_index("/dev/video2") == 2 and device_index(3) == 3
    assert device_index("rtsp://x") == "rtsp://x"
