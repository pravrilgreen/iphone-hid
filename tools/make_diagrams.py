#!/usr/bin/env python3
"""Draw the README diagrams as PNG images: python tools/make_diagrams.py

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

    def group(self, x, y, w, h, title):
        self.d.rounded_rectangle((x * K, y * K, (x + w) * K, (y + h) * K), radius=14 * K, fill=(247, 247, 249),
                                 outline=(184, 188, 198), width=2 * K)
        self.d.text(((x + 14) * K, (y + 10) * K), title, font=font(15, True), fill=MUTED)

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
    c = Canvas(1000, 760)
    c.box(90, 20, 300, 64, ["Test framework", "SDK · REST · WebSocket"], "people")
    c.box(610, 20, 300, 64, ["Operator", "web browser"], "people")
    c.group(40, 120, 920, 360, "Control box (Linux)")
    c.box(90, 160, 320, 70, ["API + web console", "HTTP · WebSocket · mDNS"])
    c.box(90, 270, 320, 70, ["Per-phone controller", "pointer model · keyboard · health"])
    c.box(90, 380, 320, 70, ["HID driver", "every frame acknowledged"])
    c.box(590, 270, 320, 70, ["Capture reader", "MJPEG passthrough, no re-encoding"])
    c.arrow([(240, 84), (240, 160)])
    c.arrow([(760, 84), (760, 110), (360, 110), (360, 160)])
    c.arrow([(250, 230), (250, 270)])
    c.arrow([(250, 340), (250, 380)])
    c.arrow([(750, 270), (750, 195), (410, 195)], "live video", at=(640, 195))
    c.box(90, 540, 320, 70, ["HID chip", "CH9329 or ESP32 bridge"], "hw")
    c.box(590, 540, 320, 70, ["HDMI capture card", "MS2109, MJPEG"], "hw")
    c.box(380, 670, 240, 70, ["iPhone", "AssistiveTouch pointer"], "phone")
    c.arrow([(250, 450), (250, 540)], "serial", at=(250, 505))
    c.arrow([(750, 540), (750, 340)], "USB video", at=(750, 505))
    c.arrow([(250, 610), (250, 705), (380, 705)], "USB or Bluetooth\nkeyboard + mouse", at=(250, 655))
    c.arrow([(620, 705), (750, 705), (750, 610)], "HDMI\n(screen mirror)", at=(750, 655))
    c.save("diagram-overview.png")


def wiring_usb_c():
    c = Canvas(1000, 560)
    c.box(350, 20, 300, 70, ["iPhone 15 or later", "USB-C"], "phone")
    c.box(330, 170, 340, 80, ["USB-C hub", "HDMI out · USB-A · PD charging in"], "hw")
    c.box(40, 185, 200, 50, ["20 W charger"], "people", size=15)
    c.box(40, 320, 280, 70, ["HDMI capture card", "MS2109"], "hw")
    c.box(680, 320, 280, 70, ["CH9329 cable", "serial in → keyboard + mouse out"], "hw")
    c.box(350, 460, 300, 70, ["Control box", "Linux: Pi 5 or mini PC"], "host")
    c.arrow([(500, 92), (500, 168)], "one cable: video out,\nkeyboard + mouse in, power", at=(500, 130), width=3, both=True)
    c.arrow([(240, 210), (330, 210)], "power", at=(285, 190))
    c.arrow([(420, 250), (420, 285), (180, 285), (180, 320)], "HDMI", at=(300, 285))
    c.arrow([(180, 390), (180, 495), (350, 495)], "USB (video)", at=(180, 440))
    c.arrow([(650, 495), (820, 495), (820, 390)], "USB (serial)", at=(820, 440))
    c.arrow([(820, 320), (820, 285), (580, 285), (580, 250)], "USB-A\n(keyboard + mouse)", at=(700, 285))
    c.save("diagram-wiring-usb-c.png")


def wiring_lightning():
    c = Canvas(1000, 560)
    c.box(330, 20, 340, 70, ["iPhone", "Lightning"], "phone")
    c.box(310, 170, 380, 80, ["Apple Lightning Digital AV Adapter", "HDMI out · its Lightning port only charges"], "hw")
    c.box(40, 185, 200, 50, ["Charger"], "people", size=15)
    c.box(40, 320, 280, 70, ["HDMI capture card", "MS2109"], "hw")
    c.box(680, 320, 280, 70, ["ESP32-S3 bridge", "Bluetooth keyboard + mouse"], "hw")
    c.box(350, 460, 300, 70, ["Control box", "Linux: Pi 5 or mini PC"], "host")
    c.arrow([(500, 90), (500, 170)], "Lightning", at=(500, 130), width=4, head=False)
    c.arrow([(240, 210), (310, 210)], "power", at=(275, 190))
    c.arrow([(420, 250), (420, 285), (180, 285), (180, 320)], "HDMI", at=(300, 285))
    c.arrow([(180, 390), (180, 495), (350, 495)], "USB (video)", at=(180, 440))
    c.arrow([(650, 495), (820, 495), (820, 390)], "USB (serial)", at=(820, 440))
    c.arrow([(820, 320), (820, 55), (670, 55)], "Bluetooth LE\nkeyboard + mouse", at=(820, 150), dashed=True)
    c.save("diagram-wiring-lightning.png")


def reliability():
    c = Canvas(1000, 600)
    xs = {"box": 160, "chip": 500, "phone": 840}
    for key, title, style in (("box", "Control box", "host"), ("chip", "HID chip", "hw"), ("phone", "iPhone", "phone")):
        c.box(xs[key] - 110, 20, 220, 56, [title], style, size=17)
        c.arrow([(xs[key], 76), (xs[key], 590)], head=False, color=(184, 188, 198))
    c.arrow([(xs["box"], 120), (xs["chip"], 120)], "1. report (checksummed frame)", at=(330, 104))
    c.arrow([(xs["chip"], 170), (xs["phone"], 170)], "2. USB / Bluetooth HID report", at=(670, 154))
    c.arrow([(xs["chip"], 220), (xs["box"], 220)], "3. ack, or an error code", at=(330, 204), dashed=True)
    notes = [
        (270, ["No ack within 500 ms?", "Ask the chip for its status first and drop every older reply:",
               "a late ack is never taken for a newer command's."]),
        (385, ["A key or button report failed?", "Send it again: it only carries state, so repeating is harmless."]),
        (480, ["A movement report failed, or went out off its time slot?",
               "Do not guess: redo the whole move from a screen corner."]),
    ]
    for y, lines in notes:
        h = 22 * len(lines) + 30
        c.box(60, y, 880, h, lines, "note", size=15)
    c.save("diagram-reliability.png")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    overview()
    wiring_usb_c()
    wiring_lightning()
    reliability()
    return 0


if __name__ == "__main__":
    sys.exit(main())
