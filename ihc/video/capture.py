"""Read an HDMI-to-USB capture card through V4L2 (OpenCV), keeping only the newest frame.

MJPEG cards (the usual MS2109/MS2130 setup) deliver JPEG bytes. With `passthrough`, OpenCV is asked
not to decode them (CAP_PROP_CONVERT_RGB = 0), so a Frame carries the card's own JPEG and streaming
forwards it to browsers without decoding or re-encoding: the capture thread costs almost no CPU.

A background thread grabs continuously (so the newest frame is always fresh), reopens the device
with backoff when it disappears (USB reset, unplug), and reports its status.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from .frame import Frame


def _default_open(device: str | int, width: int, height: int, fps: int, fourcc: str, passthrough: bool):
    cap = cv2.VideoCapture(device, cv2.CAP_V4L2)
    if not cap.isOpened():
        cap.release()
        raise OSError(f"cannot open video device {device!r} (missing, busy, or no permission: add yourself to the 'video' group)")
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fourcc))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    cap.set(cv2.CAP_PROP_FPS, fps)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    if passthrough and fourcc.upper() == "MJPG":
        cap.set(cv2.CAP_PROP_CONVERT_RGB, 0)
    return cap


def frame_from_buffer(buf: np.ndarray, seq: int, ts: float) -> Frame:
    """OpenCV hands raw MJPEG as a 1-row (or 1-column) uint8 buffer, decoded video as HxWx3."""
    if buf.ndim == 3:
        return Frame.from_image(buf, seq, ts)
    data = buf.reshape(-1).tobytes()
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        raise ValueError("capture returned neither an image nor a JPEG")
    return Frame.from_jpeg(data, seq, ts)


class V4L2Capture:
    def __init__(
        self,
        device: str | int,
        width: int = 1920,
        height: int = 1080,
        fps: int = 30,
        fourcc: str = "MJPG",
        passthrough: bool = True,
        *,
        open_fn: Callable = _default_open,
        max_failures: int = 30,
        backoff: tuple[float, float] = (0.5, 5.0),
        clock: Callable[[], float] = time.monotonic,
    ):
        self.device = device
        self.fps = fps
        self._req = (width, height, fps, fourcc, passthrough)
        self._open_fn = open_fn
        self._max_failures = max_failures
        self._backoff = backoff
        self._clock = clock
        self._cond = threading.Condition()
        self._frame: Frame | None = None
        self._seq = -1
        self._size = (width, height)
        self._stop = threading.Event()
        self.status = "starting"
        self.reopens = 0
        self.dropped = 0
        self._fps_ema = 0.0
        self._thread = threading.Thread(target=self._run, name=f"capture-{device}", daemon=True)
        self._thread.start()

    @property
    def size(self) -> tuple[int, int]:
        return self._size

    def latest(self, newer_than: int = -1, timeout: float = 1.0) -> Frame:
        with self._cond:
            ok = self._cond.wait_for(lambda: self._frame is not None and self._frame.seq > newer_than, timeout)
            if not ok:
                raise TimeoutError(f"no video frame from {self.device} ({self.status})")
            return self._frame

    def stats(self) -> dict:
        f = self._frame
        return {
            "device": str(self.device),
            "status": self.status,
            "fps": round(self._fps_ema, 1),
            "frames": self._seq + 1,
            "reopens": self.reopens,
            "dropped": self.dropped,
            "age_s": round(self._clock() - f.ts, 3) if f else None,
            "passthrough": bool(f and f.jpeg is not None),
            "size": list(self._size),
        }

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=3)

    def _run(self) -> None:
        delay = self._backoff[0]
        while not self._stop.is_set():
            try:
                cap = self._open_fn(self.device, *self._req)
            except Exception as e:
                self.status = f"error: {e}"
                self._stop.wait(delay)
                delay = min(delay * 2, self._backoff[1])
                continue
            self.status = "ok"
            delay = self._backoff[0]
            try:
                self._grab_loop(cap)
            finally:
                try:
                    cap.release()
                except Exception:
                    pass
            if not self._stop.is_set():
                self.reopens += 1
                self.status = "reopening"
                self._stop.wait(delay)

    def _grab_loop(self, cap) -> None:
        failures = 0
        last = None
        while not self._stop.is_set():
            ok, buf = cap.read()
            now = self._clock()
            if not ok or buf is None:
                failures += 1
                self.dropped += 1
                if failures >= self._max_failures:
                    self.status = "no frames"
                    return
                continue
            try:
                frame = frame_from_buffer(buf, self._seq + 1, now)
            except ValueError:
                failures += 1
                self.dropped += 1
                continue
            failures = 0
            if last is not None and now > last:
                inst = 1.0 / (now - last)
                self._fps_ema = inst if self._fps_ema == 0 else 0.9 * self._fps_ema + 0.1 * inst
            last = now
            with self._cond:
                self._seq = frame.seq
                self._frame = frame
                self._size = frame.size
                self._cond.notify_all()


def list_video_devices() -> list[dict]:
    """Capture nodes with their names and stable /dev/v4l/by-path and by-id links."""
    links: dict[str, list[str]] = {}
    for d in ("/dev/v4l/by-path", "/dev/v4l/by-id"):
        p = Path(d)
        if p.is_dir():
            for link in sorted(p.iterdir()):
                links.setdefault(os.path.realpath(link), []).append(str(link))
    out = []
    sys_dir = Path("/sys/class/video4linux")
    for node in sorted(sys_dir.glob("video*")) if sys_dir.is_dir() else []:
        dev = f"/dev/{node.name}"
        try:
            name = (node / "name").read_text().strip()
        except OSError:
            name = ""
        out.append({"device": dev, "name": name, "links": links.get(dev, [])})
    return out
