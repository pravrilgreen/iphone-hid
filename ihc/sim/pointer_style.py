"""How the simulator draws the AssistiveTouch pointer in its video: a translucent grey disc with a
coloured ring (Settings > Accessibility > Pointer Control lets the user enlarge it and pick the
border colour). Only for the picture; nothing in the control path looks at it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class PointerStyle:
    diameter_pt: float = 24.0
    border_rgb: tuple[int, int, int] = (52, 199, 89)  # iOS systemGreen
    border_pt: float = 3.0
    fill_rgb: tuple[int, int, int] = (128, 128, 128)
    fill_alpha: float = 0.45

    @property
    def border_bgr(self) -> tuple[int, int, int]:
        return self.border_rgb[::-1]

    @property
    def fill_bgr(self) -> tuple[int, int, int]:
        return self.fill_rgb[::-1]

    def radius_px(self, px_per_pt: float) -> float:
        """Outer radius (edge of the ring) in frame pixels."""
        return self.diameter_pt * px_per_pt / 2

    def border_px(self, px_per_pt: float) -> float:
        return min(self.border_pt * px_per_pt, self.radius_px(px_per_pt))


def draw_pointer(img_bgr: np.ndarray, cx: float, cy: float, px_per_pt: float,
                 style: PointerStyle = PointerStyle()) -> None:
    """Draw the pointer centred on (cx, cy) (pixel-centre convention, sub-pixel), anti-aliased, in place.

    Coverage of each pixel is approximated from its centre's distance to the circle edges, which is
    exact for straight edges and keeps the centre of mass of the drawn ring at (cx, cy).
    """
    if img_bgr.ndim != 3 or img_bgr.shape[2] != 3:
        raise ValueError("draw_pointer needs a BGR image")
    h, w = img_bgr.shape[:2]
    r_out = style.radius_px(px_per_pt)
    r_in = r_out - style.border_px(px_per_pt)
    x0, x1 = max(math.floor(cx - r_out - 1), 0), min(math.ceil(cx + r_out + 2), w)
    y0, y1 = max(math.floor(cy - r_out - 1), 0), min(math.ceil(cy + r_out + 2), h)
    if x0 >= x1 or y0 >= y1:
        return
    ys, xs = np.mgrid[y0:y1, x0:x1].astype(np.float32)
    d = np.sqrt((xs - np.float32(cx)) ** 2 + (ys - np.float32(cy)) ** 2)
    cov_out = np.clip(r_out - d + 0.5, 0.0, 1.0)
    cov_in = np.clip(r_in - d + 0.5, 0.0, 1.0) if r_in > 0 else np.zeros_like(d)
    ring = (cov_out - cov_in)[..., None]
    fill = (style.fill_alpha * cov_in)[..., None]
    patch = img_bgr[y0:y1, x0:x1].astype(np.float32)
    patch = patch * (1 - fill) + np.float32(style.fill_bgr) * fill
    patch = patch * (1 - ring) + np.float32(style.border_bgr) * ring
    img_bgr[y0:y1, x0:x1] = np.clip(patch + 0.5, 0, 255).astype(np.uint8)
