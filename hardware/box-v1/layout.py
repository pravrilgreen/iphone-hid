"""Placement of the parts of one sheet, shared by the KiCad and the SVG writers.

Coordinates are in abstract units with Y pointing down (screen / schematic
convention). `Geom` holds the unit sizes, so the same algorithm serves KiCad
(millimetres on a 1.27 mm grid) and SVG (pixels).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from netlist import NC, RAILS


@dataclass
class Geom:
    pitch: float          # pin spacing
    pin_len: float        # pin length of box symbols
    char_w: float         # width of one character of pin-name / label text
    label_pad: float      # extra room around a net label (arrow, padding)
    gap: float            # gap between placed items
    header: float         # room above a box for ref/value text
    snap: float           # placement grid (0 = none)


KICAD = Geom(pitch=2.54, pin_len=2.54, char_w=1.05, label_pad=5.08, gap=5.08, header=5.08, snap=2.54)
SVG = Geom(pitch=22.0, pin_len=22.0, char_w=7.4, label_pad=26.0, gap=36.0, header=34.0, snap=0.0)

PASSIVE_STYLES = {"R", "C", "CP", "L", "FB", "D", "D_Zener", "D_TVS", "D_Schottky", "LED", "F", "SW"}


def is_two_pin(part) -> bool:
    return part.symbol in PASSIVE_STYLES


def snap(v: float, g: Geom) -> float:
    if not g.snap:
        return v
    return round(v / g.snap) * g.snap


def ceil_to(v: float, step: float) -> float:
    return math.ceil(v / step - 1e-9) * step


@dataclass
class PinGeom:
    num: str
    x: float              # connection point relative to the symbol origin
    y: float
    side: str             # L, R, B
    name_len: int


@dataclass
class SymGeom:
    kind: str             # box, two, tp
    w: float              # body width (box) or half-span (two)
    h: float
    pins: dict            # num -> PinGeom
    extent: tuple         # (left, top, right, bottom) incl. labels, relative to origin
    pin_len: float = 0.0  # pin length of a box symbol


@dataclass
class Placement:
    part: object
    x: float
    y: float
    sym: SymGeom


@dataclass
class SheetLayout:
    block: str
    items: list = field(default_factory=list)
    width: float = 0.0
    height: float = 0.0


def label_len(net: str, g: Geom) -> float:
    if net == NC:
        return g.pitch
    bold = 1.12 if net in RAILS else 1.0
    return len(net) * g.char_w * bold + g.label_pad


def pin_len_for(part, g: Geom) -> float:
    """Pins long enough for their number text (placeholder numbers such as '?XTALI' are long)."""
    longest = max(len(p.num) for p in part.pins)
    need = longest * g.char_w * 0.85 + g.pitch * 0.5
    return max(g.pin_len, ceil_to(need, g.pitch))


def box_geom(part, g: Geom) -> SymGeom:
    left = [p for p in part.pins if p.side == "L"]
    right = [p for p in part.pins if p.side in ("R", "T")]
    bottom = [p for p in part.pins if p.side == "B"]
    maxl = max((len(p.name) for p in left), default=0)
    maxr = max((len(p.name) for p in right), default=0)
    w = max((maxl + maxr) * g.char_w + 3 * g.pitch, (len(bottom) + 1) * g.pitch, 4 * g.pitch)
    w = ceil_to(w, 2 * g.pitch)
    rows = max(len(left), len(right), 1)
    h = (rows + 1) * g.pitch
    top = -h / 2
    pl = pin_len_for(part, g)
    pins = {}
    for i, p in enumerate(left):
        pins[p.num] = PinGeom(p.num, -w / 2 - pl, top + (i + 1) * g.pitch, "L", len(p.name))
    for i, p in enumerate(right):
        pins[p.num] = PinGeom(p.num, w / 2 + pl, top + (i + 1) * g.pitch, "R", len(p.name))
    n = len(bottom)
    for i, p in enumerate(bottom):
        x = (i - (n - 1) / 2) * 2 * g.pitch
        pins[p.num] = PinGeom(p.num, x, h / 2 + pl, "B", len(p.name))
    ll = max((label_len(p.net, g) for p in left), default=0)
    rl = max((label_len(p.net, g) for p in right), default=0)
    bl = g.pitch * 3 if bottom else 0
    extent = (-w / 2 - pl - ll, -h / 2 - g.header, w / 2 + pl + rl, h / 2 + pl + bl)
    return SymGeom("box", w, h, pins, extent, pl)


def two_geom(part, g: Geom) -> SymGeom:
    half = 2 * g.pitch
    p1, p2 = part.pins
    pins = {p1.num: PinGeom(p1.num, -half, 0.0, "L", 0), p2.num: PinGeom(p2.num, half, 0.0, "R", 0)}
    extent = (-half - label_len(p1.net, g), -1.6 * g.pitch, half + label_len(p2.net, g), 1.6 * g.pitch)
    return SymGeom("two", half, g.pitch, pins, extent)


def tp_geom(part, g: Geom) -> SymGeom:
    p = part.pins[0]
    pins = {p.num: PinGeom(p.num, -g.pitch, 0.0, "L", 0)}
    extent = (-g.pitch - label_len(p.net, g), -1.6 * g.pitch, 1.6 * g.pitch, 1.6 * g.pitch)
    return SymGeom("tp", g.pitch, g.pitch, pins, extent)


def sym_geom(part, g: Geom) -> SymGeom:
    if part.symbol == "TP":
        return tp_geom(part, g)
    if is_two_pin(part):
        return two_geom(part, g)
    return box_geom(part, g)


def layout_sheet(block: str, parts: list, g: Geom, max_w: float) -> SheetLayout:
    """Shelf packing: big symbols first (tallest first), then passives in a grid."""
    sl = SheetLayout(block)
    big = [p for p in parts if not is_two_pin(p) and p.symbol != "TP"]
    small = [p for p in parts if is_two_pin(p) or p.symbol == "TP"]
    big.sort(key=lambda p: (-len(p.pins), p.ref))
    margin = g.gap
    x = margin
    y = margin + g.header
    row_h = 0.0
    used_w = 0.0
    for part in big:
        sg = sym_geom(part, g)
        l, t, r, b = sg.extent
        wd, ht = r - l, b - t
        if x + wd > max_w and x > margin:
            x = margin
            y += row_h + g.gap
            row_h = 0.0
        ox = snap(x - l, g)
        oy = snap(y - t, g)
        sl.items.append(Placement(part, ox, oy, sg))
        x = ox + r + g.gap
        used_w = max(used_w, x)
        row_h = max(row_h, oy + b - y)
    if big:
        y += row_h + 2 * g.gap
    # passives: uniform grid
    if small:
        geoms = [(p, sym_geom(p, g)) for p in small]
        cell_l = max(-sg.extent[0] for _, sg in geoms)
        cell_r = max(sg.extent[2] for _, sg in geoms)
        cell_w = cell_l + cell_r + g.gap
        cell_h = 3.2 * g.pitch
        ncol = max(1, int((max(max_w, used_w) - margin) // cell_w))
        nrow = math.ceil(len(geoms) / ncol)
        for i, (part, sg) in enumerate(geoms):
            col, row = divmod(i, nrow)      # column-major: refs read top-down
            ox = snap(margin + col * cell_w + cell_l, g)
            oy = snap(y + row * cell_h + cell_h / 2, g)
            sl.items.append(Placement(part, ox, oy, sg))
            used_w = max(used_w, ox + cell_r)
        y += nrow * cell_h
    sl.width = max(used_w, max_w * 0.5) + margin
    sl.height = y + margin
    return sl


def is_rail(net: str) -> bool:
    return net in RAILS
