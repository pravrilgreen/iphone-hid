"""Captured frames and the FrameSource protocol shared by capture, simulator and consumers.

A Frame from an MJPEG capture keeps the card's original JPEG bytes, so streaming can forward them
without re-encoding; the BGR image is decoded only when someone asks for it, once.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, Protocol, Sequence

import cv2
import numpy as np

# SOFn markers carry the frame size; C4 (DHT), C8 (JPG) and CC (DAC) share the range but do not.
_SOF_MARKERS = frozenset({0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF})
# Markers without a length field.
_STANDALONE = frozenset({0x01, *range(0xD0, 0xD8)})


def jpeg_size(data: bytes | bytearray | memoryview) -> tuple[int, int] | None:
    """(width, height) from the JPEG SOF segment without decoding, or None if not found."""
    b = memoryview(data)
    n = len(b)
    if n < 4 or b[0] != 0xFF or b[1] != 0xD8:
        return None
    i = 2
    while i + 3 < n:
        if b[i] != 0xFF:
            return None
        marker = b[i + 1]
        if marker == 0xFF:  # fill byte
            i += 1
            continue
        if marker in _STANDALONE:
            i += 2
            continue
        if marker in (0xD9, 0xDA):  # EOI, or SOS before any SOF
            return None
        length = (b[i + 2] << 8) | b[i + 3]
        if length < 2:
            return None
        if marker in _SOF_MARKERS:
            if i + 8 >= n:
                return None
            h = (b[i + 5] << 8) | b[i + 6]
            w = (b[i + 7] << 8) | b[i + 8]
            return (w, h) if w and h else None
        i += 2 + length
    return None


def decode_jpeg(data: bytes, flags: int = cv2.IMREAD_COLOR) -> np.ndarray:
    img = cv2.imdecode(np.frombuffer(data, np.uint8), flags)
    if img is None:
        raise ValueError(f"cannot decode JPEG ({len(data)} bytes)")
    return img


def encode_jpeg(img: np.ndarray, quality: int = 80) -> bytes:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, int(quality)])
    if not ok:
        raise ValueError("JPEG encoding failed")
    return buf.tobytes()


@dataclass(eq=False)
class Frame:
    """One captured frame. `ts` is time.monotonic() when it was captured.

    `image` is shared by every reader of the frame: treat it as read-only (copy before drawing).
    """

    seq: int
    ts: float
    width: int
    height: int
    jpeg: bytes | None = None
    _image: np.ndarray | None = field(default=None, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _encoded: tuple[int, bytes] | None = field(default=None, repr=False)

    @classmethod
    def from_image(cls, img: np.ndarray, seq: int = 0, ts: float | None = None) -> Frame:
        if img.ndim == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        h, w = img.shape[:2]
        return cls(seq, time.monotonic() if ts is None else ts, w, h, None, img)

    @classmethod
    def from_jpeg(cls, data: bytes, seq: int = 0, ts: float | None = None) -> Frame:
        data = bytes(data)
        size = jpeg_size(data)
        img = None
        if size is None:
            img = decode_jpeg(data)
            size = (img.shape[1], img.shape[0])
            if img.flags.writeable:
                img.flags.writeable = False
        return cls(seq, time.monotonic() if ts is None else ts, size[0], size[1], data, img)

    @property
    def size(self) -> tuple[int, int]:
        return self.width, self.height

    @property
    def decoded(self) -> bool:
        return self._image is not None

    @property
    def image(self) -> np.ndarray:
        """BGR uint8 image, decoded from `jpeg` on first access and cached."""
        img = self._image
        if img is None:
            with self._lock:
                img = self._image
                if img is None:
                    img = decode_jpeg(self.jpeg)
                    img.flags.writeable = False
                    self._image = img
        return img

    def to_jpeg(self, quality: int = 80) -> bytes:
        """The capture's own JPEG when there is one, else an encoding of `image` (cached per quality)."""
        if self.jpeg is not None:
            return self.jpeg
        enc = self._encoded
        if enc is None or enc[0] != quality:
            enc = (quality, encode_jpeg(self.image, quality))
            self._encoded = enc
        return enc[1]

    def with_seq(self, seq: int, ts: float | None = None) -> Frame:
        """The same pixels under a new sequence number and timestamp (shares the decoded image)."""
        return Frame(seq, time.monotonic() if ts is None else ts, self.width, self.height, self.jpeg,
                     self._image, _encoded=self._encoded)


def as_image(frame_or_image: Frame | np.ndarray) -> np.ndarray:
    return frame_or_image.image if isinstance(frame_or_image, Frame) else frame_or_image


class FrameSource(Protocol):
    @property
    def size(self) -> tuple[int, int]: ...

    def latest(self, newer_than: int = -1, timeout: float = 1.0) -> Frame:
        """The newest frame, waiting until one with seq > newer_than exists; TimeoutError otherwise."""
        ...

    def close(self) -> None: ...


class StaticSource:
    """Plays a list of frames/images like a camera: each `latest()` returns the next one with a new
    seq, then keeps repeating the last one (or cycles when `loop`). Never blocks."""

    def __init__(self, frames_or_images: Iterable[Frame | np.ndarray] | Frame | np.ndarray, loop: bool = False):
        if isinstance(frames_or_images, (Frame, np.ndarray)):
            frames_or_images = [frames_or_images]
        self._items: Sequence[Frame] = [
            f if isinstance(f, Frame) else Frame.from_image(f) for f in frames_or_images
        ]
        if not self._items:
            raise ValueError("StaticSource needs at least one frame")
        self.loop = loop
        self._seq = -1
        self._lock = threading.Lock()

    @property
    def size(self) -> tuple[int, int]:
        return self._items[0].size

    def latest(self, newer_than: int = -1, timeout: float = 1.0) -> Frame:
        with self._lock:
            self._seq = max(self._seq + 1, newer_than + 1)
            n = len(self._items)
            item = self._items[self._seq % n if self.loop else min(self._seq, n - 1)]
            return item.with_seq(self._seq)

    def close(self) -> None:
        pass


class CallbackSource:
    """Renders a new frame per request with `fn()` (an image or a Frame), e.g. from a simulator."""

    def __init__(self, fn: Callable[[], Frame | np.ndarray], size: tuple[int, int] | None = None):
        self._fn = fn
        self._size = size
        self._seq = -1
        self._lock = threading.Lock()

    @property
    def size(self) -> tuple[int, int]:
        if self._size is None:
            self._size = self.latest().size
        return self._size

    def latest(self, newer_than: int = -1, timeout: float = 1.0) -> Frame:
        with self._lock:
            self._seq = max(self._seq + 1, newer_than + 1)
            seq = self._seq
        out = self._fn()
        frame = out.with_seq(seq) if isinstance(out, Frame) else Frame.from_image(out, seq)
        self._size = frame.size
        return frame

    def close(self) -> None:
        pass
