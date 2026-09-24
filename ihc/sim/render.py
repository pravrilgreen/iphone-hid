"""Draw a simulated phone Layout into a BGR image (Pillow, 2x supersampled for smooth edges)."""

from __future__ import annotations

from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .phone import Element, Layout

_FONT_FILES = {
    False: ["DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "LiberationSans-Regular.ttf"],
    True: ["DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf"],
}
BLUE = (0, 122, 255)
SS = 2  # supersampling factor


@lru_cache(maxsize=64)
def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    for name in _FONT_FILES[bold]:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


class _Painter:
    def __init__(self, width_pt: float, height_pt: float, px_per_pt: float, dark: bool):
        self.k = px_per_pt * SS
        self.size = (max(1, round(width_pt * px_per_pt)), max(1, round(height_pt * px_per_pt)))
        self.img = Image.new("RGB", (self.size[0] * SS, self.size[1] * SS), (255, 255, 255))
        self.d = ImageDraw.Draw(self.img, "RGBA")
        self.dark = dark
        self.fg = (255, 255, 255) if dark else (0, 0, 0)
        self.muted = (142, 142, 147)

    def box(self, el: Element) -> tuple[float, float, float, float]:
        k = self.k
        return el.x * k, el.y * k, (el.x + el.w) * k, (el.y + el.h) * k

    def font(self, pt: float, bold: bool = False) -> ImageFont.ImageFont:
        return _font(max(6, round(pt * self.k)), bold)

    def text(self, xy_pt: tuple[float, float], s: str, pt: float, fill, bold=False, anchor="la") -> None:
        self.d.text((xy_pt[0] * self.k, xy_pt[1] * self.k), s, font=self.font(pt, bold), fill=fill, anchor=anchor)

    def rrect(self, box, radius_pt: float, fill=None, outline=None, width_pt: float = 0) -> None:
        self.d.rounded_rectangle(box, radius=radius_pt * self.k, fill=fill, outline=outline,
                                 width=max(1, round(width_pt * self.k)) if outline else 0)


def _background(p: _Painter, kind: str) -> None:
    w, h = p.img.size
    if kind in ("wallpaper", "lock"):
        top = np.array((64, 92, 180) if kind == "wallpaper" else (30, 40, 80), np.float32)
        bottom = np.array((150, 80, 170) if kind == "wallpaper" else (10, 10, 25), np.float32)
        t = np.linspace(0, 1, h, dtype=np.float32)[:, None]
        col = (top * (1 - t) + bottom * t).astype(np.uint8)
        p.img.paste(Image.fromarray(np.repeat(col[:, None, :], w, axis=1)))
    else:
        p.img.paste((0, 0, 0) if kind == "dark" else (255, 255, 255), (0, 0, w, h))


def _status_bar(p: _Painter, width_pt: float, top_pt: float, light_text: bool) -> None:
    col = (255, 255, 255) if light_text else (0, 0, 0)
    y = top_pt / 2 + 2
    p.text((34, y), "9:41", 16, col, bold=True, anchor="lm")
    k = p.k
    x1 = (width_pt - 30) * k
    p.d.rounded_rectangle((x1 - 24 * k, (y - 6) * k, x1, (y + 6) * k), radius=3 * k, outline=col, width=max(1, round(k)))
    p.d.rectangle((x1 - 22 * k, (y - 4) * k, x1 - 6 * k, (y + 4) * k), fill=col)


def _draw(p: _Painter, el: Element, on_dark: bool) -> None:
    fg = (255, 255, 255) if on_dark else (0, 0, 0)
    b = p.box(el)
    k = el.kind
    if k == "icon":
        p.rrect(b, el.w * 0.225, fill=el.color)
        glyph_col = (255, 59, 48) if el.color == (255, 255, 255) else (255, 255, 255)
        p.text(el.center, el.label[:1], el.w * 0.45, glyph_col, bold=True, anchor="mm")
        if not el.data.get("dock"):
            p.text((el.center[0], el.y + el.h + 13), el.label, 11.5, (255, 255, 255), anchor="mm")
    elif k == "dock":
        p.rrect(b, 30, fill=(255, 255, 255, 70))
    elif k == "title":
        p.text((el.x, el.y + el.h / 2), el.label, 30, fg, bold=True, anchor="lm")
    elif k == "row":
        p.text((el.x + 4, el.y + el.h / 2), el.label, 17, fg, anchor="lm")
        p.text((el.x + el.w - 4, el.y + el.h / 2), ">", 17, p.muted, anchor="rm")
        p.d.line((b[0], b[3], b[2], b[3]), fill=(60, 60, 67, 60), width=max(1, round(p.k * 0.5)))
    elif k == "back":
        p.text((el.x + 4, el.y + el.h / 2), el.label, 17, BLUE, anchor="lm")
    elif k == "button":
        if el.label == "Compose":
            p.d.ellipse(b, fill=el.color)
            p.text(el.center, "+", 28, (255, 255, 255), bold=True, anchor="mm")
        else:
            p.text(el.center, el.label, 17, BLUE, bold=True, anchor="mm")
    elif k == "editor":
        lines = (el.label + "|").split("\n")
        for i, line in enumerate(lines[-30:]):
            p.text((el.x, el.y + 4 + i * 24), line, 17, fg)
    elif k == "target":
        hit = el.data.get("hits", 0) > 0
        p.d.ellipse(b, fill=(52, 199, 89, 255) if hit else None, outline=(255, 59, 48), width=max(1, round(1.5 * p.k)))
        cx, cy = el.center
        r = 1.5 * p.k
        p.d.ellipse((cx * p.k - r, cy * p.k - r, cx * p.k + r, cy * p.k + r), fill=fg)
    elif k == "miss":
        p.d.line((b[0], b[1], b[2], b[3]), fill=(255, 59, 48), width=max(1, round(p.k)))
        p.d.line((b[0], b[3], b[2], b[1]), fill=(255, 59, 48), width=max(1, round(p.k)))
    elif k == "caption":
        p.text((el.x, el.y + el.h / 2), el.label, 13, p.muted, anchor="lm")
    elif k in ("field", "search"):
        p.rrect(b, 10, fill=(255, 255, 255, 60) if on_dark else (118, 118, 128, 60))
        text = el.label or ("Search" if k == "search" else "")
        col = fg if el.label else p.muted
        p.text((el.x + 12, el.y + el.h / 2), text + ("|" if k == "search" else ""), 17, col, anchor="lm")
    elif k == "result":
        p.rrect(b, 12, fill=(255, 255, 255, 40))
        p.rrect((b[0] + 8 * p.k, b[1] + 8 * p.k, b[0] + 44 * p.k, b[3] - 8 * p.k), 8, fill=el.color)
        p.text((el.x + 56, el.y + el.h / 2), el.label, 17, (255, 255, 255), anchor="lm")
    elif k == "card":
        p.rrect(b, 26, fill=(255, 255, 255))
        p.rrect((b[0], b[1], b[2], b[1] + 70 * p.k), 26, fill=el.color)
        p.text((el.center[0], el.y + 35), el.label, 20, (255, 255, 255), bold=True, anchor="mm")
        p.text((el.x + el.w / 2, el.y + el.h + 18), el.label, 13, (255, 255, 255), anchor="mm")
    elif k == "alert":
        p.rrect(b, 14, fill=(242, 242, 247))
        p.text((el.center[0], el.y + 40), el.label, 15, (0, 0, 0), bold=True, anchor="mm")
        p.d.line((b[0], b[3] - 44 * p.k, b[2], b[3] - 44 * p.k), fill=(60, 60, 67, 90), width=max(1, round(p.k * 0.5)))
        for i, label in enumerate(el.data.get("buttons", [])):
            p.text((el.x + el.w * (0.25 + 0.5 * i), el.y + el.h - 22), label, 17, BLUE, bold=i == 1, anchor="mm")
    elif k == "hud":
        p.rrect(b, 5, fill=(80, 80, 80, 200))
        level = el.data.get("level", 0.0)
        p.rrect((b[0], b[3] - (b[3] - b[1]) * level, b[2], b[3]), 5, fill=(255, 255, 255))
    elif k == "clock":
        p.text(el.center, el.label, 82, (255, 255, 255), bold=True, anchor="mm")
    elif k == "paragraph":
        col = (255, 255, 255) if on_dark else (60, 60, 67)
        anchor = "mm" if el.x == 0 else "la"
        xy = el.center if el.x == 0 else (el.x, el.y)
        p.d.multiline_text((xy[0] * p.k, xy[1] * p.k), _wrap(el.label, 34), font=p.font(15), fill=col, anchor=anchor)
    elif k == "heading":
        p.text((el.x, el.y), el.label, 28, fg, bold=True)
    elif k == "link":
        p.text((el.x, el.y), el.label, 17, BLUE)
    elif k == "page":
        p.d.rectangle(b, fill=(255, 255, 255))
        for gx in range(0, int(el.w), 50):
            p.d.line((gx * p.k, b[1], gx * p.k, b[3]), fill=(230, 230, 235), width=max(1, round(p.k * 0.5)))
        for gy in range(0, int(el.h), 50):
            y = b[1] + gy * p.k
            p.d.line((b[0], y, b[2], y), fill=(230, 230, 235), width=max(1, round(p.k * 0.5)))
        p.text((el.center[0], el.y + 40), el.label, 17, (0, 0, 0), bold=True, anchor="mm")
        p.text((el.center[0], el.y + 64), "keep this page open while calibrating", 12, p.muted, anchor="mm")
        for cx, cy in el.data.get("clicks", []):
            x, y = cx * p.k, (cy + el.data["top"]) * p.k
            r = 6 * p.k
            p.d.line((x - r, y, x + r, y), fill=(255, 59, 48), width=max(1, round(p.k)))
            p.d.line((x, y - r, x, y + r), fill=(255, 59, 48), width=max(1, round(p.k)))
    elif k == "indicator":
        p.rrect(b, 3, fill=(255, 255, 255) if on_dark else (0, 0, 0))


def _wrap(text: str, width: int) -> str:
    out, line = [], ""
    for word in text.split():
        if line and len(line) + 1 + len(word) > width:
            out.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    out.append(line)
    return "\n".join(out)


def render_layout(layout: Layout, width_pt: float, height_pt: float, px_per_pt: float, top_pt: float = 54.0,
                  dark: bool = False) -> np.ndarray:
    """Render at px_per_pt (frame pixels per point); returns a BGR uint8 array."""
    p = _Painter(width_pt, height_pt, px_per_pt, dark)
    _background(p, layout.background)
    on_dark = layout.background in ("wallpaper", "lock", "dark")
    for el in layout.elements:
        _draw(p, el, on_dark)
    if layout.background != "lock":
        _status_bar(p, width_pt, top_pt, on_dark or layout.dim > 0.3)
    if layout.dim:
        p.d.rectangle((0, 0, *p.img.size), fill=(0, 0, 0, round(255 * layout.dim)))
    for el in layout.overlay:
        _draw(p, el, True)
    for el in layout.hud:
        _draw(p, el, True)
    img = p.img.resize(p.size, Image.LANCZOS)
    return np.ascontiguousarray(np.asarray(img)[:, :, ::-1])
