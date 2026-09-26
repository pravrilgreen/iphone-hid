#!/usr/bin/env python3
"""Enclosure outline and connector openings of box-v1 (standard library only).

    python3 mechanical.py            # writes svg/mechanical.svg (generate.py also calls write())

Everything here is a *proposal* for the enclosure and for where the layout puts the
connectors (README §11). No layout exists yet: the layout house fixes the final connector
coordinates, returns a board outline (DXF/STEP), and the enclosure openings are then
re-dimensioned from that model. Connector heights come from the vendors' drawings and must
be checked against them; the RJ45 height (13.5 mm) is the figure used in README §11.

Coordinates: PCB top view, origin at the top-left corner of the board, x to the right,
y down the page (y = 0 is the back edge, y = PCB_H the front edge), z up from the top
surface of the PCB. Units: mm.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

HERE = os.path.dirname(os.path.abspath(__file__))

PCB_W, PCB_H, PCB_T = 90.0, 60.0, 1.6
CLEAR = 0.5            # PCB edge to inner wall
WALL = 2.0             # wall, floor and lid thickness
FLOOR = 2.0
STANDOFF = 3.0         # floor to PCB bottom
TALLEST = 13.5         # HR911105A body height above the PCB (README §11)
TOP_GAP = 1.0          # tallest part to lid
LID = 2.0
INNER_W, INNER_H = PCB_W + 2 * CLEAR, PCB_H + 2 * CLEAR
OUTER_W, OUTER_H = INNER_W + 2 * WALL, INNER_H + 2 * WALL
PCB_Z0 = FLOOR + STANDOFF + PCB_T              # height of the PCB top surface above the outside bottom
OUTER_Z = PCB_Z0 + TALLEST + TOP_GAP + LID     # outer height
MOUNT_HOLES = [(3.5, 3.5), (PCB_W - 3.5, 3.5), (3.5, PCB_H - 3.5), (PCB_W - 3.5, PCB_H - 3.5)]
MOUNT_DRILL = 2.7                              # M2.5 clearance


@dataclass
class Opening:
    ref: str          # part it serves
    wall: str         # left, right, front, back, lid
    pos: float        # centre along the wall in PCB coordinates (y for left/right, x for front/back/lid)
    z: float          # centre height above the PCB top surface (lid: y position in PCB coordinates)
    w: float          # opening width along the wall (lid: diameter)
    h: float          # opening height (lid: diameter)
    shape: str        # stadium, rect, round, dhole
    what: str
    note: str = ""


USBC = (9.6, 3.8)     # opening for a USB-C receptacle whose front face ends at the outer wall surface
OPENINGS = [
    Opening("J201", "left", 20.0, 1.6, *USBC, "stadium", "USB-C to the iPhone (Molex 105450-0101)",
            "receptacle face within 0.5 mm of the outer wall; centre height from the Molex drawing"),
    Opening("J402", "right", 16.0, 6.9, 16.4, 14.0, "rect", "RJ45 Ethernet (HanRun HR911105A)",
            "body ~16 x 13.5 mm [Likely], jack LEDs visible through the opening"),
    Opening("J401", "front", 38.0, 1.6, *USBC, "stadium", "USB-C to the PC (HRO TYPE-C-31-M-12)",
            "near Core1106 pads 22-23 (README §11)"),
    Opening("J101", "front", 74.0, 1.6, *USBC, "stadium", "USB-C PD power input (HRO TYPE-C-31-M-12)",
            "power corner, away from the DP/CSI pairs"),
    Opening("ANT", "back", 78.0, 9.0, 6.5, 6.5, "dhole", "Wi-Fi antenna, RP-SMA bulkhead (option)",
            "IPEX 1.0 pigtail from the Core1106 ANT1 connector; omit when an internal FPC antenna sits "
            "behind a non-metal window"),
    Opening("D501-D503", "lid", 46.0, 5.0, 2.0, 2.0, "round", "3 light pipes for the status LEDs",
            "at x = 42, 46, 50 mm; 0603 top-view LEDs under the lid"),
    Opening("SW501", "lid", 60.0, 5.0, 1.5, 1.5, "round", "RECOVERY button pin-hole",
            "top-actuated tactile switch; press with a pin"),
    Opening("SW502", "lid", 66.0, 5.0, 1.5, 1.5, "round", "RESET button pin-hole", ""),
]
LED_XS = (42.0, 46.0, 50.0)

# Indicative placement from README §11 (not a layout): outlines drawn for orientation only.
INDICATIVE = [
    ("U401 Core1106 30x30", 34.0, 15.0, 30.0, 30.0),
    ("U201 LT7911D", 12.0, 16.0, 7.5, 7.5),
    ("U301 CH32V305", 12.0, 36.0, 10.0, 10.0),
    ("power: U101-U106, Q101-Q103", 66.0, 40.0, 22.0, 14.0),
    ("J501 J502 J503 headers", 8.0, 1.0, 26.0, 3.0),
]
CONNECTOR_BODIES = {   # footprint-ish bodies on the top view: (x, y, w, h) in PCB coordinates
    "J201": (-0.8, 15.5, 7.5, 9.0),
    "J402": (PCB_W - 21.0 + 2.0, 8.0, 21.0, 16.0),
    "J401": (33.5, PCB_H - 7.3 + 0.8, 9.0, 7.5),
    "J101": (69.5, PCB_H - 7.3 + 0.8, 9.0, 7.5),
}


# ---------------------------------------------------------------------------
def along_outside(o: Opening) -> float:
    """Centre of the opening measured from the left end of the wall as seen from outside."""
    if o.wall == "front":
        return o.pos + CLEAR + WALL
    if o.wall == "back":
        return OUTER_W - (o.pos + CLEAR + WALL)
    if o.wall == "left":
        return o.pos + CLEAR + WALL
    if o.wall == "right":
        return OUTER_H - (o.pos + CLEAR + WALL)
    return o.pos


def z_outside(o: Opening) -> float:
    """Centre height of a wall opening above the outside bottom of the enclosure."""
    return PCB_Z0 + o.z


def openings_table() -> str:
    lines = ["| Opening | Wall / face | Centre (PCB coordinates) | Centre from outside-left corner, height above "
             "outside bottom | Size | Purpose | Note |", "|---|---|---|---|---|---|---|"]
    for o in OPENINGS:
        if o.wall == "lid":
            xs = ", ".join(f"{x:.0f}" for x in LED_XS) if o.ref.startswith("D5") else f"{o.pos:.1f}"
            pcb = f"x = {xs}, y = {o.z:.1f}"
            out = "lid, same x/y + wall offset " f"{CLEAR + WALL:.1f} mm"
            size = f"Ø{o.w:.1f}"
        else:
            axis = "y" if o.wall in ("left", "right") else "x"
            pcb = f"{axis} = {o.pos:.1f}, z = +{o.z:.1f} over PCB top"
            out = f"{along_outside(o):.1f} mm, {z_outside(o):.1f} mm"
            size = {"stadium": f"{o.w:.1f} x {o.h:.1f} slot, R{o.h / 2:.1f} ends",
                    "rect": f"{o.w:.1f} x {o.h:.1f}",
                    "dhole": f"Ø{o.w:.1f} D-hole (per bulkhead)",
                    "round": f"Ø{o.w:.1f}"}[o.shape]
        lines.append(f"| {o.ref} | {o.wall} | {pcb} | {out} | {size} | {o.what} | {o.note} |")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# SVG
# ---------------------------------------------------------------------------
S = 5.0     # px per mm
INK, DIM, PCBC, CUT, LIDC, IND = "#111827", "#6b7280", "#15803d", "#b91c1c", "#1d4ed8", "#9ca3af"


def esc(t: str) -> str:
    return str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def text(x, y, t, size=12, anchor="start", color=INK, weight="normal"):
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" text-anchor="{anchor}" fill="{color}" '
            f'font-weight="{weight}">{esc(t)}</text>')


def rect(x, y, w, h, stroke=INK, fill="none", sw=1.5, dash="", rx=0):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    r = f' rx="{rx:.1f}"' if rx else ""
    return (f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" fill="{fill}" stroke="{stroke}" '
            f'stroke-width="{sw}"{d}{r}/>')


def circle(cx, cy, r, stroke=INK, fill="none", sw=1.5):
    return f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>'


def hdim(x1, x2, y, label):
    return (f'<line x1="{x1:.1f}" y1="{y:.1f}" x2="{x2:.1f}" y2="{y:.1f}" stroke="{DIM}" stroke-width="1"/>'
            f'<line x1="{x1:.1f}" y1="{y - 5:.1f}" x2="{x1:.1f}" y2="{y + 5:.1f}" stroke="{DIM}"/>'
            f'<line x1="{x2:.1f}" y1="{y - 5:.1f}" x2="{x2:.1f}" y2="{y + 5:.1f}" stroke="{DIM}"/>'
            + text((x1 + x2) / 2, y - 4, label, 11, "middle", DIM))


def vdim(x, y1, y2, label):
    return (f'<line x1="{x:.1f}" y1="{y1:.1f}" x2="{x:.1f}" y2="{y2:.1f}" stroke="{DIM}" stroke-width="1"/>'
            f'<line x1="{x - 5:.1f}" y1="{y1:.1f}" x2="{x + 5:.1f}" y2="{y1:.1f}" stroke="{DIM}"/>'
            f'<line x1="{x - 5:.1f}" y1="{y2:.1f}" x2="{x + 5:.1f}" y2="{y2:.1f}" stroke="{DIM}"/>'
            + f'<text x="{x - 6:.1f}" y="{(y1 + y2) / 2:.1f}" font-size="11" text-anchor="middle" fill="{DIM}" '
              f'transform="rotate(-90 {x - 6:.1f} {(y1 + y2) / 2:.1f})">{esc(label)}</text>')


def cutout(cx, cy, o: Opening):
    w, h = o.w * S, o.h * S
    if o.shape == "stadium":
        return rect(cx - w / 2, cy - h / 2, w, h, CUT, "#fee2e2", 1.5, rx=h / 2)
    if o.shape == "rect":
        return rect(cx - w / 2, cy - h / 2, w, h, CUT, "#fee2e2", 1.5)
    return circle(cx, cy, w / 2, CUT, "#fee2e2")


def top_view(ox, oy):
    """Top view with the lid removed; lid openings drawn in blue."""
    out = [text(ox, oy - 14, "Top view (lid removed), mm, 1 mm = 5 px", 14, weight="bold")]
    out.append(rect(ox, oy, OUTER_W * S, OUTER_H * S, INK, "#f3f4f6", 2, rx=3 * S))
    out.append(rect(ox + WALL * S, oy + WALL * S, INNER_W * S, INNER_H * S, INK, "white", 1))
    px, py = ox + (WALL + CLEAR) * S, oy + (WALL + CLEAR) * S
    out.append(rect(px, py, PCB_W * S, PCB_H * S, PCBC, "#ecfdf5", 2, rx=2 * S))
    for x, y in MOUNT_HOLES:
        out.append(circle(px + x * S, py + y * S, MOUNT_DRILL / 2 * S, PCBC))
    for label, x, y, w, h in INDICATIVE:
        out.append(rect(px + x * S, py + y * S, w * S, h * S, IND, "none", 1.2, "5,3"))
        out.append(text(px + (x + w / 2) * S, py + (y + h / 2) * S + 4, label, 10, "middle", IND))
    for ref, (x, y, w, h) in CONNECTOR_BODIES.items():
        out.append(rect(px + x * S, py + y * S, w * S, h * S, INK, "#e5e7eb", 1.2))
        out.append(text(px + (x + w / 2) * S, py + (y + h / 2) * S + 4, ref, 11, "middle", INK, "bold"))
    for o in OPENINGS:
        if o.wall == "lid":
            xs = LED_XS if o.ref.startswith("D5") else (o.pos,)
            for x in xs:
                out.append(circle(px + x * S, py + o.z * S, o.w / 2 * S + 1, LIDC, "#dbeafe"))
            label = "LEDs" if o.ref.startswith("D5") else o.ref[-3:]
            out.append(text(px + sum(xs) / len(xs) * S, py + o.z * S + 20, label, 10, "middle", LIDC))
            continue
        # mark wall cut-outs on the top view as thick red bars in the wall
        if o.wall in ("left", "right"):
            x = ox + (0 if o.wall == "left" else (OUTER_W - WALL)) * S
            y = py + (o.pos - o.w / 2) * S
            out.append(rect(x, y, WALL * S, o.w * S, CUT, CUT, 1))
        else:
            y = oy + (0 if o.wall == "back" else (OUTER_H - WALL)) * S
            x = px + (o.pos - o.w / 2) * S
            out.append(rect(x, y, o.w * S, WALL * S, CUT, CUT, 1))
    out.append(text(px + 49 * S, py + 41 * S, "ANT1 IPEX 1.0 on the module", 10, "middle", IND))
    out.append(text(px + 49 * S, py + 43.5 * S, "(location: Core1106.pdf p.2)", 10, "middle", IND))
    out.append(hdim(ox, ox + OUTER_W * S, oy + OUTER_H * S + 36, f"outer {OUTER_W:.0f}"))
    out.append(hdim(px, px + PCB_W * S, oy + OUTER_H * S + 56, f"PCB {PCB_W:.0f}"))
    out.append(vdim(ox - 18, oy, oy + OUTER_H * S, f"outer {OUTER_H:.0f}"))
    out.append(vdim(ox - 38, py, py + PCB_H * S, f"PCB {PCB_H:.0f}"))
    out.append(text(ox + OUTER_W * S / 2, oy - 2, "back", 11, "middle", DIM))
    out.append(text(ox + OUTER_W * S / 2, oy + OUTER_H * S + 16, "front", 11, "middle", DIM))
    return out


def elevation(ox, oy, wall: str):
    length = OUTER_W if wall in ("front", "back") else OUTER_H
    out = [text(ox, oy - 10, f"{wall.capitalize()} wall, seen from outside", 13, weight="bold")]
    out.append(rect(ox, oy, length * S, OUTER_Z * S, INK, "#f3f4f6", 2))
    # PCB and lid lines
    ypcb = oy + (OUTER_Z - PCB_Z0) * S
    out.append(f'<line x1="{ox:.1f}" y1="{ypcb:.1f}" x2="{ox + length * S:.1f}" y2="{ypcb:.1f}" '
               f'stroke="{PCBC}" stroke-width="1.2" stroke-dasharray="6,4"/>')
    out.append(text(ox + 4, ypcb - 3, "PCB top", 10, "start", PCBC))
    ylid = oy + LID * S
    out.append(f'<line x1="{ox:.1f}" y1="{ylid:.1f}" x2="{ox + length * S:.1f}" y2="{ylid:.1f}" '
               f'stroke="{DIM}" stroke-width="1" stroke-dasharray="3,3"/>')
    for o in OPENINGS:
        if o.wall != wall:
            continue
        cx = ox + along_outside(o) * S
        cy = oy + (OUTER_Z - z_outside(o)) * S
        out.append(cutout(cx, cy, o))
        label = f"{o.ref} {o.w:.1f}x{o.h:.1f}" if o.shape != "dhole" else f"{o.ref} Ø{o.w:.1f}"
        if o.h > 10:   # tall opening: label inside
            out.append(text(cx, cy + 4, label, 11, "middle", CUT, "bold"))
        else:
            out.append(text(cx, cy - o.h * S / 2 - 5, label, 11, "middle", CUT, "bold"))
        out.append(text(cx, oy + OUTER_Z * S + 14, f"{along_outside(o):.1f}", 10, "middle", DIM))
    out.append(vdim(ox - 12, oy, oy + OUTER_Z * S, f"{OUTER_Z:.1f}"))
    out.append(hdim(ox, ox + length * S, oy + OUTER_Z * S + 30, f"{length:.0f}"))
    return out


def render() -> str:
    W, H = 1180, 1120
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
           'font-family="DejaVu Sans, Arial, Helvetica, sans-serif">',
           f'<rect x="0" y="0" width="{W}" height="{H}" fill="white"/>',
           text(20, 32, "box-v1 enclosure: outline and connector openings (proposal, generated by mechanical.py)",
                20, weight="bold"),
           text(20, 54, f"Outer {OUTER_W:.0f} x {OUTER_H:.0f} x {OUTER_Z:.1f} mm, wall/lid {WALL:.0f} mm, "
                        f"PCB {PCB_W:.0f} x {PCB_H:.0f} x {PCB_T} mm on {STANDOFF:.0f} mm standoffs (4x M2.5). "
                        "Positions follow README §11 and must be re-checked against the final layout.", 12, color=DIM),
           text(20, 72, "Red = wall cut-out, blue = lid hole, grey dashed = indicative part placement (not a layout).",
                12, color=DIM)]
    out += top_view(80, 125)
    out += elevation(640, 150, "left")
    out += elevation(640, 360, "right")
    out += elevation(80, 610, "front")
    out += elevation(640, 610, "back")
    # notes
    notes = [
        "Notes",
        "1. USB-C: the receptacle front face should end within 0.5 mm of the outer wall surface (connector overhangs",
        "   the PCB edge, or the wall is thinned locally); otherwise open the wall for the plug overmold (>= 12.5 x 6.6 mm).",
        "2. Connector centre heights are nominal: take them from the Molex 105450-0101, HRO TYPE-C-31-M-12 and HanRun",
        "   HR911105A drawings once the layout house has placed the parts.",
        "3. Wi-Fi: the Core1106 Wi-Fi variant has an IPEX 1.0 connector (ANT1), no usable on-module antenna is assumed.",
        "   With an aluminium case use the RP-SMA bulkhead + IPEX pigtail, or an FPC antenna behind a plastic window.",
        "4. Thermal: ~2 W of losses (bucks, LT7911D, RV1106); a 1-2 mm gap pad from the LT7911D/buck area to the lid",
        "   or floor is recommended in an aluminium case (README risk H4).",
        "5. Debug headers J501/J502/J503 are reached with the lid off; no wall opening is planned for them.",
    ]
    y = 900
    for i, n in enumerate(notes):
        out.append(text(80, y + i * 20, n, 13 if i == 0 else 12, weight="bold" if i == 0 else "normal"))
    out.append("</svg>")
    return "\n".join(out) + "\n"


def write(path: str | None = None) -> str:
    path = path or os.path.join(HERE, "svg", "mechanical.svg")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(render())
    return path


if __name__ == "__main__":
    print("wrote", write())
