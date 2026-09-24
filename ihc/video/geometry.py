"""Where the iPhone screen sits inside the capture frame, and conversions to the public coordinates.

Pixel convention used across ihc.video: pixel (i, j) of an image is centred on (i, j) and covers
[i - 0.5, i + 0.5] x [j - 0.5, j + 0.5] (OpenCV's convention). A ScreenRect whose first image column
is c0 and which is w columns wide therefore has x = c0 - 0.5; its centre is x + w / 2.

Public API coordinates are normalized: (0, 0) is the top-left corner of the screen image and (1, 1)
the bottom-right corner, whatever the phone orientation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# iPhone 15/16 (and most 6.1" models) portrait width in points.
DEFAULT_PHONE_WIDTH_PT = 393.0


@dataclass(frozen=True)
class ScreenRect:
    """The phone's screen image in frame pixels (floats; see the module docstring for the convention)."""

    x: float
    y: float
    w: float
    h: float

    @classmethod
    def from_pixels(cls, c0: int, r0: int, c1: int, r1: int) -> ScreenRect:
        """Rect covering image columns c0..c1-1 and rows r0..r1-1 (slice bounds)."""
        return cls(c0 - 0.5, r0 - 0.5, float(c1 - c0), float(r1 - r0))

    @property
    def orientation(self) -> str:
        return "portrait" if self.h >= self.w else "landscape"

    @property
    def aspect(self) -> float:
        return self.w / self.h if self.h else 0.0

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h

    @property
    def center(self) -> tuple[float, float]:
        return self.x + self.w / 2, self.y + self.h / 2

    def norm_to_frame(self, nx: float, ny: float) -> tuple[float, float]:
        return self.x + nx * self.w, self.y + ny * self.h

    def frame_to_norm(self, fx: float, fy: float) -> tuple[float, float]:
        return (fx - self.x) / self.w, (fy - self.y) / self.h

    def contains(self, fx: float, fy: float, margin: float = 0.0) -> bool:
        """True if the frame point lies inside the rect shrunk by `margin` pixels (negative grows it)."""
        return (
            self.x + margin <= fx <= self.right - margin
            and self.y + margin <= fy <= self.bottom - margin
        )

    def clip(self, fx: float, fy: float) -> tuple[float, float]:
        """Clamp a frame point onto the rect (onto the centres of its border pixels)."""
        return (
            min(max(fx, self.x + 0.5), self.right - 0.5),
            min(max(fy, self.y + 0.5), self.bottom - 0.5),
        )

    def clipped_to(self, width: int, height: int) -> ScreenRect:
        """The part of the rect that lies inside a width x height frame."""
        x0, y0 = max(self.x, -0.5), max(self.y, -0.5)
        x1, y1 = min(self.right, width - 0.5), min(self.bottom, height - 0.5)
        return ScreenRect(x0, y0, max(0.0, x1 - x0), max(0.0, y1 - y0))

    def inset(self, d: float) -> ScreenRect:
        """Shrink by `d` pixels on every side (negative grows it)."""
        return ScreenRect(self.x + d, self.y + d, max(0.0, self.w - 2 * d), max(0.0, self.h - 2 * d))

    def as_int_box(self) -> tuple[int, int, int, int]:
        """(c0, r0, c1, r1) slice bounds of the pixels whose centres lie in the rect: img[r0:r1, c0:c1]."""
        c0 = math.ceil(self.x + 0.5 - 1e-6)
        r0 = math.ceil(self.y + 0.5 - 1e-6)
        c1 = math.floor(self.right + 0.5 + 1e-6)
        r1 = math.floor(self.bottom + 0.5 + 1e-6)
        return c0, r0, max(c0, c1), max(r0, r1)

    def px_per_pt(self, phone_width_pt: float = DEFAULT_PHONE_WIDTH_PT) -> float:
        """Frame pixels per iOS point, from the short side of the rect (the phone's portrait width)."""
        return min(self.w, self.h) / phone_width_pt

    def distance(self, other: ScreenRect) -> float:
        """Largest displacement of any edge between two rects, in pixels."""
        return max(
            abs(self.x - other.x), abs(self.y - other.y),
            abs(self.right - other.right), abs(self.bottom - other.bottom),
        )

    def to_dict(self) -> dict:
        return {"x": round(self.x, 2), "y": round(self.y, 2), "w": round(self.w, 2), "h": round(self.h, 2),
                "orientation": self.orientation}


def fit_screen_rect(frame_size: tuple[int, int], phone_size: tuple[float, float], landscape: bool = False) -> ScreenRect:
    """Where a mirrored phone screen lands in the capture frame: scaled to fit and centred, which is
    how the iPhone's HDMI mirror fills a video mode (black bars on the sides in portrait). No image
    analysis: the rig config can override it when a card crops or overscans."""
    fw, fh = frame_size
    pw, ph = phone_size if not landscape else (phone_size[1], phone_size[0])
    scale = min(fw / pw, fh / ph)
    sw, sh = round(pw * scale), round(ph * scale)
    c0, r0 = (fw - sw) // 2, (fh - sh) // 2
    return ScreenRect.from_pixels(c0, r0, c0 + sw, r0 + sh)
