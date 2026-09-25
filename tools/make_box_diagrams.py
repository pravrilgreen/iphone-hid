#!/usr/bin/env python3
"""Draw the custom-box design diagrams (docs/research/custom-box.md): python tools/make_box_diagrams.py

Same drawing kit and style as tools/make_diagrams.py.
"""

from __future__ import annotations

import sys

from make_diagrams import OUT, Canvas


def box_architecture():
    """Variant B: the standalone box, everything on one board."""
    c = Canvas(1000, 800)
    c.box(350, 20, 300, 64, ["iPhone 15+", "USB-C · AssistiveTouch"], "phone")
    c.box(710, 20, 250, 64, ["USB-C PD in", "12 V · 30 W charger"], "people", size=15)
    c.group(20, 120, 960, 540, "iHC box (one PCB)")
    c.box(150, 150, 700, 64, ["USB-C to the iPhone", "CC · DP lanes · USB 2.0 D+/D- · VBUS"], "hw", size=15)
    c.arrow([(500, 84), (500, 150)], "one cable", at=(500, 117), width=3, both=True)

    # what rides on each wire of that port
    c.box(45, 290, 395, 74, ["LT7911D", "PD + DP Alt Mode sink · DP → MIPI CSI-2"], "hw", size=15)
    c.box(455, 290, 230, 74, ["CH32V305 (HID MCU)", "USB 2.0 HS · 125 µs"], "hw", size=14)
    c.box(710, 290, 230, 74, ["Charger pass-through", "PD in → VBUS to phone"], "hw", size=14)
    c.arrow([(175, 214), (175, 290)], "CC", at=(175, 252), both=True)
    c.arrow([(345, 214), (345, 290)], "DP lanes", at=(345, 252))
    c.arrow([(570, 214), (570, 290)], "USB 2.0", at=(570, 252), both=True)
    c.arrow([(825, 290), (825, 214)], "VBUS", at=(825, 252))
    c.arrow([(900, 84), (900, 290)])

    # the SoC
    c.box(45, 420, 640, 80, ["RV1106 SoC (Luckfox Core1106 module)",
                             "capture · crop · H.264 low-delay encoder · API · web console"], "host", size=15)
    c.arrow([(242, 364), (242, 420)], "CSI-2 (4 lanes)", at=(242, 392))
    c.arrow([(570, 364), (570, 420)], "SPI / UART", at=(570, 392), both=True)

    # network and the PC port
    c.box(45, 560, 250, 64, ["Ethernet 100M", "PHY inside the SoC"], "host", size=14)
    c.box(315, 560, 180, 64, ["Wi-Fi", "SDIO module"], "host", size=14)
    c.box(515, 560, 425, 64, ["USB-C to a PC (SoC's USB port)", "UVC video · USB network · control"], "host", size=14)
    c.arrow([(170, 500), (170, 560)], both=True)
    c.arrow([(405, 500), (405, 560)], both=True)
    c.arrow([(600, 500), (600, 560)], both=True)

    c.box(300, 710, 400, 64, ["Remote systems", "SDK · REST · WebRTC / browser"], "people")
    c.arrow([(170, 624), (170, 742), (300, 742)], "LAN", at=(170, 690), both=True)
    c.arrow([(405, 624), (405, 710)], "Wi-Fi", at=(405, 668), both=True)
    c.arrow([(727, 624), (727, 742), (700, 742)], "USB", at=(727, 690), both=True)
    c.save("box-architecture.png")


def box_dock():
    """Variant A: a USB dock for a PC; no Linux on the box."""
    c = Canvas(1000, 780)
    c.box(350, 20, 300, 64, ["iPhone 15+", "USB-C"], "phone")
    c.box(700, 20, 260, 64, ["USB-C PD in", "wall charger"], "people", size=15)
    c.group(20, 120, 960, 540, "iHC dock (one PCB, no Linux)")
    c.box(150, 150, 700, 64, ["USB-C to the iPhone", "CC · DP lanes · USB 2.0 D+/D- · VBUS"], "hw", size=15)
    c.arrow([(500, 84), (500, 150)], "one cable", at=(500, 117), width=3, both=True)
    c.arrow([(900, 84), (900, 130), (800, 130), (800, 150)], "VBUS", at=(900, 108))

    c.box(60, 290, 340, 74, ["LT8711HE", "Type-C → HDMI · PD + Alt Mode inside"], "hw", size=15)
    c.box(600, 290, 340, 74, ["CH32V305 (HID MCU)", "HS device to the iPhone"], "hw", size=15)
    c.arrow([(230, 214), (230, 290)], "CC + DP", at=(230, 252))
    c.arrow([(770, 214), (770, 290)], "USB 2.0", at=(770, 252), both=True)

    c.box(60, 420, 340, 74, ["MS2131", "HDMI → USB 3 UVC · HDMI loop-out"], "hw", size=15)
    c.box(420, 420, 160, 74, ["HDMI out", "optional"], "people", size=15)
    c.box(600, 420, 340, 74, ["USB 3 hub chip", "one cable to the PC"], "hw", size=15)
    c.arrow([(230, 364), (230, 420)], "HDMI", at=(230, 392))
    c.arrow([(400, 470), (420, 470)], dashed=True)
    c.arrow([(770, 364), (770, 420)], "control", at=(770, 392), both=True)
    c.arrow([(230, 494), (230, 530), (700, 530), (700, 494)], "UVC video", at=(460, 530))

    c.box(760, 570, 180, 64, ["USB-C", "to the PC"], "host", size=15)
    c.arrow([(850, 494), (850, 570)], both=True)
    c.box(640, 700, 320, 64, ["PC / mini PC", "runs the ihc server"], "people")
    c.arrow([(850, 634), (850, 700)], "USB 3", at=(850, 668), width=3, both=True)
    c.save("box-dock.png")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    box_architecture()
    box_dock()
    return 0


if __name__ == "__main__":
    sys.exit(main())
