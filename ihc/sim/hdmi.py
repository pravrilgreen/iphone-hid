"""What the capture card would deliver for a SimPhone: an HDMI mirror inside a fixed video mode.

Modelled on how mirroring and cheap USB capture cards behave:
- the phone image is scaled to fit the video mode and centred, leaving black bars (pillarbox in
  portrait);
- cards often output limited-range video, so "black" is about 16, not 0 (`limited_range`);
- frames arrive at the card's frame rate and show the phone as it was `latency` seconds ago;
- frames are MJPEG, so they carry compression artifacts and the JPEG bytes can be streamed as-is;
- the AssistiveTouch pointer is drawn (or left out with pointer_visible=False: whether iOS mirrors
  the pointer over HDMI is still to be verified; the control path never depends on seeing it).
Rendering happens on demand: nothing is drawn while nobody asks for frames.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

import cv2
import numpy as np

from ..hid.fake import SimPointer
from ..video.frame import Frame, encode_jpeg
from ..video.geometry import fit_screen_rect
from .pointer_style import PointerStyle, draw_pointer
from .phone import SimPhone
from .render import render_layout


class SimHdmiCapture:
    def __init__(
        self,
        phone: SimPhone,
        pointer: SimPointer,
        *,
        size: tuple[int, int] = (1920, 1080),
        fps: float = 30.0,
        latency: float = 0.08,
        jpeg_quality: int = 80,
        limited_range: bool = True,
        pointer_visible: bool = True,
        pointer_autohide: float | None = None,
        style: PointerStyle = PointerStyle(),
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.phone = phone
        self.pointer = pointer
        self.fps = fps
        self.latency = latency
        self.jpeg_quality = jpeg_quality
        self.limited_range = limited_range
        self.pointer_visible = pointer_visible
        self.pointer_autohide = pointer_autohide
        self.style = style
        self.signal = True
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._size = size
        m = phone.model
        self.screen_rect = fit_screen_rect(size, (m.width_pt, m.height_pt))
        c0, r0, c1, r1 = self.screen_rect.as_int_box()
        self._offset = (c0, r0)
        self._screen_px = (c1 - c0, r1 - r0)
        self.px_per_pt = (c1 - c0) / m.width_pt
        self._screens: dict[tuple, np.ndarray] = {}
        self._shown: tuple | None = None
        self._last: Frame | None = None
        self._last_key: tuple | None = None
        self._seq = -1
        self.rendered = 0

    @property
    def size(self) -> tuple[int, int]:
        return self._size

    def close(self) -> None:
        pass

    def latest(self, newer_than: int = -1, timeout: float = 1.0) -> Frame:
        with self._lock:
            now = self._clock()
            period = 1.0 / self.fps
            last = self._last
            if last is not None and last.seq > newer_than and now - last.ts < period:
                return last
            if last is not None and now < last.ts + period:
                wait = last.ts + period - now
                if wait > timeout:
                    raise TimeoutError("no new frame in time")
                self._sleep(wait)
                now = self._clock()
            self._last = self._render(now)
            return self._last

    def _screen(self, t: float) -> tuple[tuple, np.ndarray]:
        """The phone screen as shown at time t: a UI change is visible only once its animation
        delay has passed; until then the previously shown image stays up."""
        ph = self.phone
        with ph._lock:
            version, changed_at, hud = ph.version, ph.changed_at, ph.hud_until > t
            key = (version, hud)
            if changed_at > t and self._shown is not None and self._shown in self._screens:
                return self._shown, self._screens[self._shown]
            if key not in self._screens:
                layout = ph.layout()
                img = render_layout(layout, ph.model.width_pt, ph.model.height_pt, self.px_per_pt, ph.top, ph.dark)
                if img.shape[1::-1] != self._screen_px:
                    img = cv2.resize(img, self._screen_px, interpolation=cv2.INTER_AREA)
                self._screens[key] = img
                self.rendered += 1
                for old in list(self._screens)[:-3]:
                    del self._screens[old]
        self._shown = key
        return key, self._screens[key]

    def _render(self, now: float) -> Frame:
        self._seq += 1
        W, H = self._size
        black = 16 if self.limited_range else 0
        if not self.signal:
            self._last_key = None  # the next real frame must be drawn, not reused
            img = np.full((H, W, 3), black, np.uint8)
            return Frame(self._seq, now, W, H, encode_jpeg(img, self.jpeg_quality), img)
        t = now - self.latency
        key, screen = self._screen(t)
        px, py = self.pointer.position_at(t)
        visible = self.pointer_visible
        if visible and self.pointer_autohide is not None and self.pointer.history:
            visible = t - self.pointer.history[-1][0] < self.pointer_autohide
        frame_key = (key, round(px, 2), round(py, 2), visible)
        if frame_key == self._last_key and self._last is not None:
            return self._last.with_seq(self._seq, now)
        screen = screen.copy()
        if visible:
            # point coordinates to pixel-centre coordinates of the screen image
            draw_pointer(screen, px * self.px_per_pt - 0.5, py * self.px_per_pt - 0.5, self.px_per_pt, self.style)
        img = np.full((H, W, 3), 0, np.uint8)
        x0, y0 = self._offset
        img[y0 : y0 + screen.shape[0], x0 : x0 + screen.shape[1]] = screen
        if self.limited_range:
            img = cv2.convertScaleAbs(img, alpha=219 / 255, beta=16)
        data = encode_jpeg(img, self.jpeg_quality)
        self._last_key = frame_key
        return Frame(self._seq, now, W, H, data, None)

    def pointer_truth(self, t: float | None = None) -> tuple[float, float]:
        """Where the pointer centre is drawn in frame pixels (for tests and accuracy reports)."""
        px, py = self.pointer.position_at(self._clock() - self.latency if t is None else t)
        return self.screen_rect.x + px * self.px_per_pt, self.screen_rect.y + py * self.px_per_pt
