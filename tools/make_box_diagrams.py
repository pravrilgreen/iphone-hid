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
    c.group(20, 120, 960, 540, "iHC box (one PCB)", align="right")
    c.box(150, 150, 700, 64, ["USB-C to the iPhone", "CC · DP lanes · USB 2.0 D+/D- · VBUS"], "hw", size=15)
    c.arrow([(500, 84), (500, 150)], "one cable", at=(500, 117), width=3, both=True)

    # what rides on each wire of that port
    c.box(45, 290, 190, 74, ["PD controller", "DP Alt Mode sink"], "hw", size=14)
    c.box(250, 290, 190, 74, ["Power", "5 V to the iPhone"], "hw", size=14)
    c.box(455, 290, 230, 74, ["DP → MIPI CSI-2", "bridge chip"], "hw", size=14)
    c.box(710, 290, 230, 74, ["HID MCU", "USB 2.0 HS · 125 µs"], "hw", size=14)
    c.arrow([(175, 214), (175, 290)], "CC", at=(175, 252))
    c.arrow([(345, 290), (345, 214)], "VBUS", at=(345, 252))
    c.arrow([(570, 214), (570, 290)], "DP lanes", at=(570, 252))
    c.arrow([(825, 214), (825, 290)], "USB 2.0", at=(825, 252), both=True)

    # the SoC and its inputs
    c.box(455, 420, 485, 80, ["SoC (Linux)", "capture · crop · H.264 encoder · API"], "host", size=15)
    c.box(250, 420, 190, 80, ["USB-C PD in", "12 V · 30 W"], "people", size=14)
    c.arrow([(570, 364), (570, 420)], "CSI-2", at=(570, 392))
    c.arrow([(825, 364), (825, 420)], "SPI", at=(825, 392), both=True)
    c.arrow([(345, 420), (345, 364)])

    # network and outputs
    c.box(45, 560, 190, 64, ["HDMI out", "optional"], "people", size=14)
    c.box(455, 560, 155, 64, ["Ethernet"], "host", size=14)
    c.box(625, 560, 155, 64, ["Wi-Fi"], "host", size=14)
    c.box(795, 560, 145, 64, ["USB-C", "to a PC"], "host", size=14)
    c.arrow([(500, 500), (500, 530), (140, 530), (140, 560)], "optional", at=(300, 530), dashed=True)
    c.arrow([(532, 500), (532, 560)], both=True)
    c.arrow([(702, 500), (702, 560)], both=True)
    c.arrow([(940, 327), (965, 327), (965, 592), (940, 592)], "MCU's\n2nd USB", at=(965, 460), dashed=True)

    c.box(380, 710, 400, 64, ["Remote systems", "SDK · REST · WebRTC / browser"], "people")
    c.arrow([(532, 624), (532, 710)], "LAN", at=(532, 668), both=True)
    c.arrow([(702, 624), (702, 710)], "Wi-Fi", at=(702, 668), both=True)
    c.arrow([(867, 624), (867, 742), (780, 742)], "USB", at=(867, 690), dashed=True, both=True)
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

    c.box(60, 290, 340, 74, ["Type-C → HDMI converter", "PD + DP Alt Mode inside"], "hw", size=15)
    c.box(600, 290, 340, 74, ["HID MCU", "HS device to the iPhone"], "hw", size=15)
    c.arrow([(230, 214), (230, 290)], "CC + DP", at=(230, 252))
    c.arrow([(770, 214), (770, 290)], "USB 2.0", at=(770, 252), both=True)

    c.box(60, 420, 340, 74, ["HDMI → USB UVC", "capture chip"], "hw", size=15)
    c.box(420, 420, 160, 74, ["HDMI out", "optional"], "people", size=15)
    c.box(600, 420, 340, 74, ["USB hub", "one cable to the PC"], "hw", size=15)
    c.arrow([(230, 364), (230, 420)], "HDMI", at=(230, 392))
    c.arrow([(400, 327), (500, 327), (500, 420)], "splitter", at=(470, 327), dashed=True)
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
