#!/usr/bin/env python3
"""Draw the README diagrams as PNG images: python scripts/make_diagrams.py

Plain images rather than Mermaid blocks, because GitHub's mobile app shows Mermaid as source code.
Laid out by hand for a phone-width screen (about 1000 px wide at 2x).
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "images"
K = 2  # drawn at 2x for sharp text

STYLES = {  # fill, border
    "host": ((236, 243, 255), (59, 111, 216)),
    "hw": ((255, 244, 224), (217, 144, 26)),
    "phone": ((233, 247, 238), (46, 158, 87)),
    "people": ((243, 243, 245), (120, 124, 134)),
    "note": ((255, 246, 214), (224, 177, 0)),
}
INK = (28, 28, 30)
LINE = (74, 79, 90)
MUTED = (99, 99, 102)


def font(size: int, bold: bool = False):
    try:
        return ImageFont.truetype("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf", size * K)
    except OSError:
        return ImageFont.load_default(size=size * K)


class Canvas:
    def __init__(self, w: int, h: int):
        self.img = Image.new("RGB", (w * K, h * K), (255, 255, 255))
        self.d = ImageDraw.Draw(self.img)

    def group(self, x, y, w, h, title, align="left"):
        self.d.rounded_rectangle((x * K, y * K, (x + w) * K, (y + h) * K), radius=14 * K, fill=(247, 247, 249),
                                 outline=(184, 188, 198), width=2 * K)
        f = font(15, True)
        tx = (x + 14) * K if align == "left" else (x + w - 14) * K - self.d.textlength(title, font=f)
        self.d.text((tx, (y + 10) * K), title, font=f, fill=MUTED)

    def box(self, x, y, w, h, lines, style="host", size=16):
        """A rounded box centred text; `lines`: title first (bold), then detail lines."""
        fill, border = STYLES[style]
        self.d.rounded_rectangle((x * K, y * K, (x + w) * K, (y + h) * K), radius=10 * K, fill=fill,
                                 outline=border, width=2 * K)
        fonts = [font(size, True)] + [font(size - 3)] * (len(lines) - 1)
        heights = [f.getbbox("Hg")[3] for f in fonts]
        gap = 5 * K
        total = sum(heights) + gap * (len(lines) - 1)
        ty = (y + h / 2) * K - total / 2
        for text, f, lh in zip(lines, fonts, heights):
            tw = self.d.textlength(text, font=f)
            self.d.text(((x + w / 2) * K - tw / 2, ty), text, font=f, fill=INK if f is fonts[0] else MUTED)
            ty += lh + gap
        return (x, y, w, h)

    def arrow(self, points, label=None, at=None, width=2, dashed=False, head=True, color=LINE, both=False):
        pts = [(px * K, py * K) for px, py in points]
        for a, b in zip(pts, pts[1:]):
            if dashed:
                self._dashed(a, b, color, width * K)
            else:
                self.d.line((a, b), fill=color, width=width * K)
        if head:
            self._head(pts[-2], pts[-1], color, width)
        if both:
            self._head(pts[1], pts[0], color, width)
        if label:
            if at is None:
                (ax, ay), (bx, by) = points[0], points[1]
                at = ((ax + bx) / 2, (ay + by) / 2)
            self.label(at, label)

    def label(self, at, text, size=13):
        f = font(size)
        lines = text.split("\n")
        lh = f.getbbox("Hg")[3] + 3 * K
        w = max(self.d.textlength(t, font=f) for t in lines) + 12 * K
        h = lh * len(lines) + 6 * K
        cx, cy = at[0] * K, at[1] * K
        self.d.rounded_rectangle((cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2), radius=6 * K, fill=(255, 255, 255))
        for i, t in enumerate(lines):
            tw = self.d.textlength(t, font=f)
            self.d.text((cx - tw / 2, cy - h / 2 + 3 * K + i * lh), t, font=f, fill=MUTED)

    def text(self, xy, text, size=14, bold=False, color=INK, anchor="la"):
        self.d.text((xy[0] * K, xy[1] * K), text, font=font(size, bold), fill=color, anchor=anchor)

    def _head(self, a, b, color, width):
        import math

        ang = math.atan2(b[1] - a[1], b[0] - a[0])
        L, spread = (11 + 2 * width) * K, 0.42
        p1 = (b[0] - L * math.cos(ang - spread), b[1] - L * math.sin(ang - spread))
        p2 = (b[0] - L * math.cos(ang + spread), b[1] - L * math.sin(ang + spread))
        self.d.polygon([b, p1, p2], fill=color)

    def _dashed(self, a, b, color, width, dash=9 * K, gap=6 * K):
        import math

        length = math.dist(a, b)
        if not length:
            return
        ux, uy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
        t = 0.0
        while t < length:
            e = min(t + dash, length)
            self.d.line(((a[0] + ux * t, a[1] + uy * t), (a[0] + ux * e, a[1] + uy * e)), fill=color, width=width)
            t = e + gap

    def save(self, name: str):
        self.img.save(OUT / name, optimize=True)


def overview():
    c = Canvas(1000, 800)
    c.box(90, 20, 300, 64, ["Test framework", "Python SDK or HTTP API"], "people")
    c.box(610, 20, 300, 64, ["Operator", "web browser"], "people")
    c.group(40, 120, 920, 400, "Orange Pi 5 Plus running ihcd, one board per iPhone", align="right")
    c.box(90, 160, 320, 70, ["API and web console", "HTTP, WebSocket, mDNS"])
    c.box(90, 270, 320, 70, ["Touch engine", "live touch, gestures, keys, buttons"])
    c.box(90, 420, 320, 70, ["USB gadget", "absolute pointer and keyboard"], "hw")
    c.box(590, 420, 320, 70, ["HDMI input", "raw frames, 1080p60"], "hw")
    c.box(590, 270, 320, 70, ["Screen reader", "phone area cut out, JPEG"])
    c.arrow([(240, 84), (240, 160)])
    c.arrow([(760, 84), (760, 110), (360, 110), (360, 160)])
    c.arrow([(250, 230), (250, 270)])
    c.arrow([(250, 340), (250, 420)])
    c.arrow([(750, 420), (750, 340)])
    c.arrow([(750, 270), (750, 195), (410, 195)], "live screen", at=(640, 195))
    c.box(330, 580, 340, 70, ["USB-C hub", "HDMI out, USB-A, charging in"], "hw")
    c.box(380, 710, 240, 70, ["iPhone", "AssistiveTouch pointer"], "phone")
    c.arrow([(250, 490), (250, 615), (330, 615)], "USB\ntouch and keys", at=(250, 550))
    c.arrow([(670, 615), (750, 615), (750, 490)], "HDMI\n(screen mirror)", at=(750, 550))
    c.arrow([(500, 650), (500, 710)], "one USB-C cable", at=(500, 680), width=3, both=True)
    c.save("diagram-overview.png")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    overview()
    return 0


if __name__ == "__main__":
    sys.exit(main())
