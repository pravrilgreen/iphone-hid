#!/usr/bin/env python3
"""Printed circuit board of box v1, generated from netlist.py.

Writes kicad/box-v1.kicad_pcb: the board outline and mounting holes, 4 copper layers, the design
rules and net classes of the stack-up in README §5/§11, every footprint placed, copper zones, and
(with --route) the tracks from Freerouting. U201 (LT7911D) is placed with a provisional footprint and
left unrouted: its pins 24-64 wait for the datasheet (README §10.1), so its pads carry no nets while
the rest of the board is routed, and get them back afterwards (DRC then lists them as unconnected).

Needs KiCad 7's Python module (`pcbnew`, from the kicad package) and the KiCad 7.0.11 footprint
library; routing needs Java 17+ and the Freerouting 1.9.0 jar (run under xvfb-run when there is no
display: Freerouting 1.9 always opens its window).

    KICAD7_FOOTPRINT_DIR=/path/to/kicad-footprints python3 pcb.py --check-only   # placement checks
    KICAD7_FOOTPRINT_DIR=/path/to/kicad-footprints python3 pcb.py                # board, zones, DRC
    FREEROUTING_JAR=/path/to/freerouting-1.9.0.jar python3 pcb.py --route --passes 30 --render svg

Board coordinates in this file are millimetres from the top-left corner of the board, x to the
right (towards the rear of the box), y down. The iPhone port is on the left (front) edge; network,
PC and power ports are on the right (rear) edge.
"""

from __future__ import annotations

import argparse
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile

import pcbnew

import netlist as NL
from placement import (W, H, CORNER_R, MH, FIDUCIALS, RESERVED, MOD, PLACE, OUTCAPS, INCAPS,
                       HINTS)

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "kicad", "box-v1.kicad_pcb")
FP_DIR = os.environ.get("KICAD7_FOOTPRINT_DIR", "/usr/share/kicad/footprints")
JAR = os.environ.get("FREEROUTING_JAR", "")

OX, OY = 100.0, 60.0        # where the board's top-left corner sits on the KiCad page

UNROUTED_PARTS = {"U201"}   # pads get their nets back only after routing
# nets left to the final layout of the LT7911D region (DP lanes, AUX, CSI: pin map pending)
UNROUTED_NETS = re.compile(r"^(SS_(TX|RX)[12]_[PN]|PHONE_SBU[12]|LT_AUX_[PN]|CSI_.*)$")


def mm(v: float) -> int:
    return pcbnew.FromMM(v)


def pt(x: float, y: float) -> pcbnew.VECTOR2I:
    return pcbnew.VECTOR2I(mm(OX + x), mm(OY + y))


def board_xy(v) -> tuple:
    return pcbnew.ToMM(v.x) - OX, pcbnew.ToMM(v.y) - OY


# ---------------------------------------------------------------------------------------------
# Footprints the KiCad 7 library does not have
# ---------------------------------------------------------------------------------------------
def _pad(fp, num, x, y, w, h, shape=pcbnew.PAD_SHAPE_ROUNDRECT, layers=None, rratio=0.25):
    p = pcbnew.PAD(fp)
    p.SetNumber(num)
    p.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
    p.SetShape(shape)
    if shape == pcbnew.PAD_SHAPE_ROUNDRECT:
        p.SetRoundRectRadiusRatio(rratio)
    p.SetSize(pcbnew.VECTOR2I(mm(w), mm(h)))
    p.SetPosition(pcbnew.VECTOR2I(mm(x), mm(y)))
    p.SetPos0(pcbnew.VECTOR2I(mm(x), mm(y))) if hasattr(p, "SetPos0") else None
    ls = pcbnew.LSET()
    for layer in layers or (pcbnew.F_Cu, pcbnew.F_Paste, pcbnew.F_Mask):
        ls.AddLayer(layer)
    p.SetLayerSet(ls)
    fp.Add(p)
    return p


def _rect(fp, layer, x1, y1, x2, y2, width=0.05):
    for (a, b, c, d) in ((x1, y1, x2, y1), (x2, y1, x2, y2), (x2, y2, x1, y2), (x1, y2, x1, y1)):
        s = pcbnew.FP_SHAPE(fp, pcbnew.SHAPE_T_SEGMENT)
        s.SetStart0(pcbnew.VECTOR2I(mm(a), mm(b)))
        s.SetEnd0(pcbnew.VECTOR2I(mm(c), mm(d)))
        s.SetLayer(layer)
        s.SetWidth(mm(width))
        s.SetDrawCoord()
        fp.Add(s)


def _silk_dot(fp, x, y, r=0.15):
    s = pcbnew.FP_SHAPE(fp, pcbnew.SHAPE_T_CIRCLE)
    s.SetStart0(pcbnew.VECTOR2I(mm(x), mm(y)))
    s.SetEnd0(pcbnew.VECTOR2I(mm(x + r), mm(y)))
    s.SetLayer(pcbnew.F_SilkS)
    s.SetWidth(mm(0.3))
    s.SetDrawCoord()
    fp.Add(s)


def _new_fp(board, name):
    fp = pcbnew.FOOTPRINT(board)
    fp.SetFPID(pcbnew.LIB_ID("box-v1", name))
    fp.SetAttributes(pcbnew.FP_SMD)
    return fp


def fp_lt7911d(board):
    """QFN-64 7.5 x 7.5 mm, 0.4 mm pitch, pin 1 top-left, counter-clockwise. PROVISIONAL: the pad
    length and the exposed pad (5.4 mm here) must be checked against the datasheet R1.4 drawing."""
    fp = _new_fp(board, "LT7911D_QFN-64-1EP_7.5x7.5mm_P0.4mm")
    body, pitch, n = 7.5, 0.4, 16
    c = body / 2 - 0.05                  # pad centre from the body centre (pad 0.2 x 0.7, 0.3 mm toe)
    first = -(n - 1) / 2 * pitch
    for i in range(n):
        o = first + i * pitch
        _pad(fp, str(1 + i), -c, o, 0.7, 0.2)                 # left, top -> bottom
        _pad(fp, str(17 + i), o, c, 0.2, 0.7)                 # bottom, left -> right
        _pad(fp, str(33 + i), c, -o, 0.7, 0.2)                # right, bottom -> top
        _pad(fp, str(49 + i), -o, -c, 0.2, 0.7)               # top, right -> left
    ep = _pad(fp, "65", 0, 0, 5.4, 5.4, rratio=0.05,
              layers=(pcbnew.F_Cu, pcbnew.F_Mask))
    for ix in (-1.5, 0, 1.5):                                 # paste windows, ~50 % coverage
        for iy in (-1.5, 0, 1.5):
            _pad(fp, "65", ix, iy, 1.1, 1.1, rratio=0.1, layers=(pcbnew.F_Paste,))
    del ep
    _rect(fp, pcbnew.F_CrtYd, -4.25, -4.25, 4.25, 4.25)
    _rect(fp, pcbnew.F_Fab, -3.75, -3.75, 3.75, 3.75, 0.1)
    for x1, y1, x2, y2 in ((-3.85, -3.85, -3.3, -3.85), (-3.85, -3.85, -3.85, -3.3),
                           (3.85, -3.85, 3.3, -3.85), (3.85, -3.85, 3.85, -3.3),
                           (3.85, 3.85, 3.3, 3.85), (3.85, 3.85, 3.85, 3.3),
                           (-3.85, 3.85, -3.3, 3.85), (-3.85, 3.85, -3.85, 3.3)):
        s = pcbnew.FP_SHAPE(fp, pcbnew.SHAPE_T_SEGMENT)
        s.SetStart0(pcbnew.VECTOR2I(mm(x1), mm(y1)))
        s.SetEnd0(pcbnew.VECTOR2I(mm(x2), mm(y2)))
        s.SetLayer(pcbnew.F_SilkS)
        s.SetWidth(mm(0.12))
        s.SetDrawCoord()
        fp.Add(s)
    _silk_dot(fp, -4.3, -3.4)
    return fp


def fp_core1106(board):
    """Luckfox Core1106: 30 x 30 mm, 112 castellated pads at 1.0 mm pitch, 28 per side, each pad
    0.7 x 1.5 mm centred on the module edge; pin 1 top-left, counter-clockwise (README §4.4)."""
    fp = _new_fp(board, "Luckfox_Core1106_Castellated_30x30mm_P1.0mm")
    half, n, pitch = 15.0, 28, 1.0
    first = -(n - 1) / 2 * pitch
    for i in range(n):
        o = first + i * pitch
        _pad(fp, str(1 + i), -half, o, 1.5, 0.7)
        _pad(fp, str(29 + i), o, half, 0.7, 1.5)
        _pad(fp, str(57 + i), half, -o, 1.5, 0.7)
        _pad(fp, str(85 + i), -o, -half, 0.7, 1.5)
    _rect(fp, pcbnew.F_CrtYd, -15.95, -15.95, 15.95, 15.95)
    _rect(fp, pcbnew.F_Fab, -15, -15, 15, 15, 0.1)
    _rect(fp, pcbnew.F_SilkS, -15.95, -15.95, 15.95, 15.95, 0.12)
    _silk_dot(fp, -16.5, -14.0)
    return fp


def fp_essop10(board):
    """CH224K: ESSOP-10 3.9 x 4.9 mm, 1.0 mm pitch, exposed pad 2.1 x 3.3 mm = pad 11 (KiCad 8's
    SSOP-10-1EP_3.9x4.9mm_P1mm_EP2.1x3.3mm; built on KiCad 7's SSOP-10_3.9x4.9mm_P1.00mm)."""
    fp = pcbnew.FootprintLoad(os.path.join(FP_DIR, "Package_SO.pretty"), "SSOP-10_3.9x4.9mm_P1.00mm")
    fp.SetFPID(pcbnew.LIB_ID("box-v1", "SSOP-10-1EP_3.9x4.9mm_P1mm_EP2.1x3.3mm"))
    _pad(fp, "11", 0, 0, 2.1, 3.3, rratio=0.05, layers=(pcbnew.F_Cu, pcbnew.F_Mask))
    _pad(fp, "11", 0, 0, 1.7, 2.7, rratio=0.05, layers=(pcbnew.F_Paste,))
    return fp


CUSTOM = {
    "box-v1:LT7911D_QFN-64-1EP_7.5x7.5mm_P0.4mm": fp_lt7911d,
    "box-v1:Luckfox_Core1106_Castellated_30x30mm_P1.0mm": fp_core1106,
    "Package_SO:SSOP-10-1EP_3.9x4.9mm_P1mm_EP2.1x3.3mm": fp_essop10,
}


def load_footprint(board, fpid: str):
    if fpid in CUSTOM:
        return CUSTOM[fpid](board)
    lib, name = fpid.split(":")
    path = os.path.join(HERE, "footprints", "box-v1.pretty") if lib == "box-v1" else \
        os.path.join(FP_DIR, lib + ".pretty")
    fp = pcbnew.FootprintLoad(path, name)
    if fp is None:
        sys.exit(f"footprint {fpid} not found in {FP_DIR} (set KICAD7_FOOTPRINT_DIR)")
    return fp


# ---------------------------------------------------------------------------------------------
# Placement. The big parts (connectors, ICs, inductors, bulk parts) sit where the signal flow wants
# them: (x, y, rotation in degrees counter-clockwise). Every other part is small; `legalize` puts it
# next to the pin it serves (decoupling capacitors radially at their supply pin, the rest at the
# centroid of the pins it connects), on the nearest free spot.
# ---------------------------------------------------------------------------------------------
def _fp_box(fp):
    """Courtyard box of a footprint relative to its origin at rotation 0 (x0, y0, x1, y1)."""
    fp.BuildCourtyardCaches()
    c = fp.GetCourtyard(pcbnew.F_CrtYd)
    bb = c.BBox() if c.OutlineCount() else fp.GetBoundingBox(False, False)
    ox, oy = fp.GetPosition().x, fp.GetPosition().y
    return (pcbnew.ToMM(bb.GetLeft() - ox), pcbnew.ToMM(bb.GetTop() - oy),
            pcbnew.ToMM(bb.GetRight() - ox), pcbnew.ToMM(bb.GetBottom() - oy))


def _rot_box(box, rot):
    x0, y0, x1, y1 = box
    r = rot % 360
    if r == 0:
        return box
    if r == 90:        # (x, y) -> (y, -x)
        return (y0, -x1, y1, -x0)
    if r == 180:
        return (-x1, -y1, -x0, -y0)
    return (-y1, x0, -y0, x1)   # 270: (x, y) -> (-y, x)


class Occupancy:
    """Placed courtyards in 4 mm buckets, for fast overlap tests."""
    B = 4.0

    def __init__(self):
        self.cells = {}

    def _keys(self, r):
        for i in range(int(math.floor(r[0] / self.B)), int(math.floor(r[2] / self.B)) + 1):
            for j in range(int(math.floor(r[1] / self.B)), int(math.floor(r[3] / self.B)) + 1):
                yield (i, j)

    def add(self, r):
        for k in self._keys(r):
            self.cells.setdefault(k, []).append(r)

    def free(self, r, gap=0.0):
        if r[0] < 0.4 or r[1] < 0.4 or r[2] > W - 0.4 or r[3] > H - 0.4:
            return False
        for k in self._keys(r):
            for o in self.cells.get(k, ()):
                if r[0] < o[2] + gap and o[0] < r[2] + gap and r[1] < o[3] + gap and o[1] < r[3] + gap:
                    return False
        return True


def legalize(occ, fp, box0, tx, ty, rots, max_r=14.0, step=0.2):
    """Put fp on the free spot nearest to (tx, ty) with one of the rotations; returns True if placed."""
    ring = 0
    while ring * step <= max_r:
        cands = []
        if ring == 0:
            cands = [(tx, ty)]
        else:
            d = ring * step
            n = max(8, int(2 * math.pi * d / step))
            cands = [(tx + d * math.cos(2 * math.pi * i / n), ty + d * math.sin(2 * math.pi * i / n))
                     for i in range(n)]
        for x, y in cands:
            for rot in rots:
                b = _rot_box(box0, rot)
                r = (x + b[0], y + b[1], x + b[2], y + b[3])
                if occ.free(r):
                    fp.SetOrientationDegrees(rot)
                    fp.SetPosition(pt(x, y))
                    occ.add(r)
                    return True
        ring += 1
    return False


RADIAL_ROT = {(1, 0): 180, (-1, 0): 0, (0, -1): 270, (0, 1): 90}   # pad 1 towards the IC pad


def _pad_outward(ic, padnum):
    pad = ic.FindPadByNumber(padnum)
    px, py = board_xy(pad.GetPosition())
    cx, cy = board_xy(ic.GetPosition())
    dx, dy = px - cx, py - cy
    if abs(dx) >= abs(dy):
        return px, py, (1 if dx > 0 else -1), 0
    return px, py, 0, (1 if dy > 0 else -1)


def place_parts(board, design, nets):
    fps, missing = {}, []
    for part in design.parts:
        fp = load_footprint(board, part.footprint)
        fp.SetReference(part.ref)
        fp.SetValue(part.value)
        board.Add(fp)
        fps[part.ref] = fp
        if part.dnp:
            fp.SetAttributes(fp.GetAttributes() | pcbnew.FP_EXCLUDE_FROM_BOM | pcbnew.FP_EXCLUDE_FROM_POS_FILES)
        pins = {p.num: p for p in part.pins}
        pad_nums = {pad.GetNumber() for pad in fp.Pads()}
        for pad in fp.Pads():
            pin = pins.get(pad.GetNumber())
            if pin and pin.net not in (NL.NC, None, ""):
                pad.SetNet(nets[pin.net])
        for num, pin in pins.items():
            if num not in pad_nums and pin.net not in (NL.NC, None, ""):
                missing.append(f"{part.ref}.{num} ({pin.name}) -> {pin.net}")

    boxes0 = {ref: _fp_box(fp) for ref, fp in fps.items()}
    occ = Occupancy()
    for x, y in MH:
        occ.add((x - 3.0, y - 3.0, x + 3.0, y + 3.0))
    for x, y in FIDUCIALS:
        occ.add((x - 1.3, y - 1.3, x + 1.3, y + 1.3))
    for r in RESERVED:
        occ.add(r)
    placed = set()

    def put(ref, x, y, rot):
        fp = fps[ref]
        fp.SetOrientationDegrees(rot)
        fp.SetPosition(pt(x, y))
        b = _rot_box(boxes0[ref], rot)
        occ.add((x + b[0], y + b[1], x + b[2], y + b[3]))
        placed.add(ref)

    for ref, (x, y, rot) in PLACE.items():
        put(ref, x, y, rot)

    # bucks: output capacitors beside the inductor output pad, input capacitors beside VIN
    for lref, (caps, padnum, side) in OUTCAPS.items():
        px, py = board_xy(fps[lref].FindPadByNumber(padnum).GetPosition())
        lb = _rot_box(boxes0[lref], PLACE[lref][2])
        lx = board_xy(fps[lref].GetPosition())[0]
        big = boxes0[caps[0]][2] - boxes0[caps[0]][0] > 4
        cw = (boxes0[caps[0]][3] - boxes0[caps[0]][1])     # height at rot 0 = width at rot 90
        x = lx + (lb[2] if side > 0 else lb[0]) + side * (cw / 2 + 0.05)
        pitch = (boxes0[caps[0]][2] - boxes0[caps[0]][0]) + 0.05
        for i, c in enumerate(caps):
            put(c, x, py + (i - (len(caps) - 1) / 2) * pitch + (2.0 if big else 0.0), 90 if side > 0 else 270)
    for ic, caps in INCAPS.items():
        vx, vy = board_xy(fps[ic].FindPadByNumber("2").GetPosition())
        ib = _rot_box(boxes0[ic], PLACE[ic][2])
        ix = board_xy(fps[ic].GetPosition())[0]
        cw = boxes0[caps[0]][3] - boxes0[caps[0]][1]
        for i, c in enumerate(caps):
            put(c, ix + ib[0] - cw / 2 - 0.05 - i * (cw + 0.05), vy - 0.4, 90)

    # decoupling capacitors: radially at their supply pin
    decaps = []
    for part in design.parts:
        if part.ref not in fps:
            continue
        for pin in part.pins:
            for c in pin.decap:
                if c not in placed and pin.num in {p.GetNumber() for p in fps[part.ref].Pads()}:
                    decaps.append((c, part.ref, pin.num))
    for c, ic, pad in decaps:
        if c in placed:
            continue
        px, py, ux, uy = _pad_outward(fps[ic], pad)
        d = 1.2 + (boxes0[c][2] - boxes0[c][0]) / 2
        rot = RADIAL_ROT[(ux, uy)]
        if legalize(occ, fps[c], boxes0[c], px + ux * d, py + uy * d, [rot, (rot + 90) % 360], max_r=6):
            placed.add(c)

    # everything else: at the centroid of the placed pads on its signal nets (rails count less)
    by_net = {}
    for ref, fp in fps.items():
        for pad in fp.Pads():
            if pad.GetNetname():
                by_net.setdefault(pad.GetNetname(), []).append((ref, pad))
    rest = [p for p in design.parts if p.ref not in placed]
    # parts touching an already placed IC pin first
    rest.sort(key=lambda p: (0 if any(n.net in by_net and len(by_net[n.net]) <= 4 for n in p.pins) else 1, p.ref))
    unplaced = []
    for part in rest:
        ref = part.ref
        if ref in HINTS and HINTS[ref]:
            tx, ty = HINTS[ref]
        else:
            sx = sy = sw = 0.0
            for pin in part.pins:
                net = pin.net
                if net in (NL.NC, NL.GND, None, "") or net not in by_net:
                    continue
                others = [(r, pd) for r, pd in by_net[net] if r != ref and r in placed]
                if not others:
                    continue
                wgt = 1.0 if len(by_net[net]) <= 6 else 0.15
                for r, pd in others:
                    x, y = board_xy(pd.GetPosition())
                    sx += x * wgt / len(others)
                    sy += y * wgt / len(others)
                    sw += wgt / len(others)
            if sw == 0:
                unplaced.append(ref)
                continue
            tx, ty = sx / sw, sy / sw
        if legalize(occ, fps[ref], boxes0[ref], tx, ty, [0, 90]):
            placed.add(ref)
        else:
            unplaced.append(ref)
    tidy_silk(fps, design)
    return fps, missing, unplaced


SMALL_FP = re.compile(r"^(R|C|L|LED)_(0402|0603|0805|1206)|^Fuse_1206|^D_SOD|^SolderJumper")


def tidy_silk(fps, design):
    """Silkscreen: small references for ICs, connectors, switches, LEDs and test points (test points
    also show their net); passives keep their reference on the fabrication layer only."""
    parts = {p.ref: p for p in design.parts}
    for ref, fp in fps.items():
        name = fp.GetFPID().GetLibItemName().wx_str()
        r, v = fp.Reference(), fp.Value()
        v.SetVisible(False)
        r.SetTextSize(pcbnew.VECTOR2I(mm(0.8), mm(0.8)))
        r.SetTextThickness(mm(0.12))
        if SMALL_FP.match(name) and not ref.startswith("D5"):
            r.SetLayer(pcbnew.F_Fab)
            r.SetTextSize(pcbnew.VECTOR2I(mm(0.4), mm(0.4)))
            r.SetTextThickness(mm(0.06))
            r.SetPosition(fp.GetPosition())
        if ref.startswith("TP"):
            v.SetVisible(True)
            v.SetLayer(pcbnew.F_SilkS)
            v.SetTextSize(pcbnew.VECTOR2I(mm(0.8), mm(0.8)))
            v.SetTextThickness(mm(0.12))
            v.SetText(parts[ref].pins[0].net)


# ---------------------------------------------------------------------------------------------
def apply_netclasses(board):
    """Net classes from netlist.py on every net of a board, with the widths and clearances of
    NL.NETCLASSES. KiCad keeps pattern-based assignments in the project, and a board loaded again in
    the same process can come back with every net in the default class, so they are set here."""
    ns = board.GetDesignSettings().m_NetSettings
    classes = {}
    for name, (tw, cl, dw, dg, vd, vdr) in NL.NETCLASSES.items():
        if name == "Default":
            nc = ns.m_DefaultNetClass
        else:
            nc = ns.m_NetClasses[name] if ns.m_NetClasses.has_key(name) else pcbnew.NETCLASS(name)
        nc.SetTrackWidth(mm(tw))
        nc.SetClearance(mm(cl))
        nc.SetDiffPairWidth(mm(dw))
        nc.SetDiffPairGap(mm(dg))
        nc.SetViaDiameter(mm(vd))
        nc.SetViaDrill(mm(vdr))
        if name != "Default":
            ns.m_NetClasses[name] = nc
        classes[name] = nc
    for name, net in board.GetNetsByName().items():
        if str(name):
            net.SetNetClass(classes[NL.netclass_of(str(name))])
    return classes


def build_board(design):
    board = pcbnew.BOARD()
    board.SetCopperLayerCount(4)
    board.SetLayerType(pcbnew.In1_Cu, pcbnew.LT_POWER)       # L2: solid GND reference plane
    ds = board.GetDesignSettings()
    ds.SetCopperLayerCount(4)
    ds.m_TrackMinWidth = mm(0.1)
    ds.m_MinClearance = mm(0.1)
    ds.m_ViasMinSize = mm(0.45)
    ds.m_MinThroughDrill = mm(0.25)
    ds.m_HoleClearance = mm(0.25)
    ds.m_CopperEdgeClearance = mm(0.25)
    ds.m_SolderMaskMinWidth = mm(0.1)
    ns = ds.m_NetSettings
    classes = {}
    for name, (tw, cl, dw, dg, vd, vdr) in NL.NETCLASSES.items():
        nc = ns.m_DefaultNetClass if name == "Default" else pcbnew.NETCLASS(name)
        nc.SetTrackWidth(mm(tw))
        nc.SetClearance(mm(cl))
        nc.SetDiffPairWidth(mm(dw))
        nc.SetDiffPairGap(mm(dg))
        nc.SetViaDiameter(mm(vd))
        nc.SetViaDrill(mm(vdr))
        if name != "Default":
            ns.m_NetClasses[name] = nc
        classes[name] = nc

    nets = {}
    for name in sorted(design.net_pins()):
        n = pcbnew.NETINFO_ITEM(board, name)
        board.Add(n)
        n.SetNetClass(classes[NL.netclass_of(name)])
        nets[name] = n
    return board, nets, classes


# ---------------------------------------------------------------------------------------------
def outline(board):
    """Board edge with rounded corners, mounting holes, fiducials, silkscreen labels."""
    r = CORNER_R
    segs = [((r, 0), (W - r, 0)), ((W, r), (W, H - r)), ((W - r, H), (r, H)), ((0, H - r), (0, r))]
    for a, b in segs:
        s = pcbnew.PCB_SHAPE(board, pcbnew.SHAPE_T_SEGMENT)
        s.SetStart(pt(*a))
        s.SetEnd(pt(*b))
        s.SetLayer(pcbnew.Edge_Cuts)
        s.SetWidth(mm(0.1))
        board.Add(s)
    k = r * (1 - math.sqrt(0.5))
    for a0, m, a1 in (((0, r), (k, k), (r, 0)), ((W - r, 0), (W - k, k), (W, r)),
                      ((W, H - r), (W - k, H - k), (W - r, H)), ((r, H), (k, H - k), (0, H - r))):
        a = pcbnew.PCB_SHAPE(board, pcbnew.SHAPE_T_ARC)
        a.SetArcGeometry(pt(*a0), pt(*m), pt(*a1))
        a.SetLayer(pcbnew.Edge_Cuts)
        a.SetWidth(mm(0.1))
        board.Add(a)
    for i, (x, y) in enumerate(MH, start=1):
        fp = pcbnew.FootprintLoad(os.path.join(FP_DIR, "MountingHole.pretty"), "MountingHole_2.7mm_M2.5")
        fp.SetReference(f"H{i}")
        fp.SetValue("M2.5")
        fp.Value().SetVisible(False)
        fp.SetAttributes(pcbnew.FP_EXCLUDE_FROM_BOM | pcbnew.FP_EXCLUDE_FROM_POS_FILES | pcbnew.FP_BOARD_ONLY)
        fp.SetPosition(pt(x, y))
        board.Add(fp)
    for i, (x, y) in enumerate(FIDUCIALS, start=1):
        fp = pcbnew.FootprintLoad(os.path.join(FP_DIR, "Fiducial.pretty"), "Fiducial_1mm_Mask2mm")
        fp.SetReference(f"FID{i}")
        fp.Value().SetVisible(False)
        fp.SetAttributes(pcbnew.FP_EXCLUDE_FROM_BOM | pcbnew.FP_BOARD_ONLY)
        fp.SetPosition(pt(x, y))
        board.Add(fp)
    for text, x, y, size, rot in (("iphone-hid box v1 (draft layout)", 48.0, 33.0, 1.5, 0),
                                  ("github.com/pravrilgreen/iphone-hid", 48.0, 36.0, 1.0, 0)):
        t = pcbnew.PCB_TEXT(board)
        t.SetText(text)
        t.SetPosition(pt(x, y))
        t.SetLayer(pcbnew.B_SilkS)
        t.SetMirrored(True)
        t.SetTextSize(pcbnew.VECTOR2I(mm(size), mm(size)))
        t.SetTextThickness(mm(size * 0.15))
        t.SetTextAngleDegrees(rot)
        board.Add(t)


def _keepout(board, pts, name, tracks=True, vias=True, pour=True, layers=None):
    k = pcbnew.ZONE(board)
    k.SetIsRuleArea(True)
    k.SetDoNotAllowVias(vias)
    k.SetDoNotAllowTracks(tracks)
    k.SetDoNotAllowCopperPour(pour)
    k.SetDoNotAllowPads(False)
    k.SetDoNotAllowFootprints(False)
    ls = pcbnew.LSET()
    for layer in layers or (pcbnew.F_Cu, pcbnew.In1_Cu, pcbnew.In2_Cu, pcbnew.B_Cu):
        ls.AddLayer(layer)
    k.SetLayerSet(ls)
    o = k.Outline()
    o.NewOutline()
    for x, y in pts:
        o.Append(mm(OX + x), mm(OY + y))
    k.SetZoneName(name)
    board.Add(k)


def _circle(x, y, r, n=16):
    return [(x + r * math.cos(2 * math.pi * i / n), y + r * math.sin(2 * math.pi * i / n)) for i in range(n)]


def rule_areas(board):
    """No vias under the castellated module (its underside is bare), no copper pours or tracks on
    the top layer under it except at its pads; a clear ring around each fiducial and each
    unplated hole (the router does not know the 0.3 mm hole-to-copper rule)."""
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            if pad.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH:
                x, y = board_xy(pad.GetPosition())
                r = pcbnew.ToMM(max(pad.GetDrillSize().x, pad.GetDrillSize().y)) / 2 + 0.35
                _keepout(board, _circle(x, y, r), f"{fp.GetReference()} hole: clear", pour=False)
    for i, (fx, fy) in enumerate(FIDUCIALS, start=1):
        k = pcbnew.ZONE(board)
        k.SetIsRuleArea(True)
        k.SetDoNotAllowVias(True)
        k.SetDoNotAllowTracks(True)
        k.SetDoNotAllowCopperPour(True)
        k.SetDoNotAllowPads(False)
        k.SetDoNotAllowFootprints(False)
        k.SetLayer(pcbnew.F_Cu)
        o = k.Outline()
        o.NewOutline()
        for a in range(16):
            o.Append(mm(OX + fx + 1.6 * math.cos(a * math.pi / 8)), mm(OY + fy + 1.6 * math.sin(a * math.pi / 8)))
        k.SetZoneName(f"FID{i}: clear")
        board.Add(k)
    x0, y0 = MOD[0] - 13.8, MOD[1] - 13.8
    x1, y1 = MOD[0] + 13.8, MOD[1] + 13.8
    z = pcbnew.ZONE(board)
    z.SetIsRuleArea(True)
    z.SetDoNotAllowVias(True)
    z.SetDoNotAllowTracks(False)
    z.SetDoNotAllowPads(False)
    z.SetDoNotAllowCopperPour(False)
    z.SetDoNotAllowFootprints(False)
    ls = pcbnew.LSET()
    for layer in (pcbnew.F_Cu, pcbnew.In1_Cu, pcbnew.In2_Cu, pcbnew.B_Cu):
        ls.AddLayer(layer)
    z.SetLayerSet(ls)
    o = z.Outline()
    o.NewOutline()
    for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
        o.Append(mm(OX + x), mm(OY + y))
    z.SetZoneName("module: no vias")
    board.Add(z)
    z2 = pcbnew.ZONE(board)
    z2.SetIsRuleArea(True)
    z2.SetDoNotAllowVias(False)
    z2.SetDoNotAllowTracks(True)
    z2.SetDoNotAllowPads(False)
    z2.SetDoNotAllowCopperPour(True)
    z2.SetDoNotAllowFootprints(False)
    z2.SetLayer(pcbnew.F_Cu)
    o2 = z2.Outline()
    o2.NewOutline()
    for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
        o2.Append(mm(OX + x), mm(OY + y))
    z2.SetZoneName("module: no top copper")
    board.Add(z2)


def _nets(board):
    return {str(k): v for k, v in board.GetNetsByName().items()}


def prerouted(board):
    """Short fixed tracks the router cannot find on its own: the A6-B6 (D+) and A7-B7 (D-) links of
    the 16-pin PC receptacle J401, whose pads alternate D+/D-/D+/D- in one row. D+ closes on the
    connector side of the row, D- on the board side."""
    fp = next(f for f in board.GetFootprints() if f.GetReference() == "J401")
    nets = _nets(board)

    def pad(n):
        return board_xy(fp.FindPadByNumber(n).GetPosition())

    def track(net, pts, w=0.25):
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            t = pcbnew.PCB_TRACK(board)
            t.SetStart(pt(x0, y0))
            t.SetEnd(pt(x1, y1))
            t.SetWidth(mm(w))
            t.SetLayer(pcbnew.F_Cu)
            t.SetNet(nets[net])
            t.SetLocked(True)
            board.Add(t)
    (xa6, ya6), (xb6, yb6), (xa7, ya7), (xb7, yb7) = pad("A6"), pad("B6"), pad("A7"), pad("B7")
    out = xa6 + 1.2     # beyond the pad row, under the receptacle body
    inn = xa6 - 1.2
    track("PC_USB_DP", [(xb6, yb6), (out, yb6), (out, ya6), (xa6, ya6)])
    track("PC_USB_DN", [(xa7, ya7), (inn, ya7), (inn, yb7), (xb7, yb7)])


def stitch_gnd(board, step=3.0):
    """GND vias on a grid wherever they clear every other copper item, tying the GND pours of all
    four layers together (added after routing)."""
    gnd = _nets(board)["GND"]
    via_r, gap = 0.275, 0.25
    obstacles = []      # (kind, geometry) of non-GND copper in board mm
    for fp in board.GetFootprints():
        for p in fp.Pads():
            if p.GetNetCode() == gnd.GetNetCode() and p.GetAttribute() == pcbnew.PAD_ATTRIB_SMD:
                continue          # drilled GND pads stay obstacles: holes need their own spacing
            bb = p.GetBoundingBox()
            obstacles.append(("box", (pcbnew.ToMM(bb.GetLeft()) - OX, pcbnew.ToMM(bb.GetTop()) - OY,
                                      pcbnew.ToMM(bb.GetRight()) - OX, pcbnew.ToMM(bb.GetBottom()) - OY)))
    for t in board.GetTracks():
        if t.GetNetCode() == gnd.GetNetCode() and t.GetClass() != "PCB_VIA":
            continue
        if t.GetClass() == "PCB_VIA":
            x, y = board_xy(t.GetPosition())
            obstacles.append(("circle", (x, y, pcbnew.ToMM(t.GetWidth()) / 2)))
        else:
            (x0, y0), (x1, y1) = board_xy(t.GetStart()), board_xy(t.GetEnd())
            obstacles.append(("seg", (x0, y0, x1, y1, pcbnew.ToMM(t.GetWidth()) / 2)))
    keep = [z for z in board.Zones() if z.GetIsRuleArea() and z.GetDoNotAllowVias()]
    for fp in board.GetFootprints():
        keep += [z for z in fp.Zones() if z.GetIsRuleArea() and z.GetDoNotAllowVias()]
    cells = {}
    for o in obstacles:
        kind, g = o
        if kind == "box":
            x0, y0, x1, y1 = g
        elif kind == "circle":
            x0, y0, x1, y1 = g[0] - g[2], g[1] - g[2], g[0] + g[2], g[1] + g[2]
        else:
            x0, y0, x1, y1 = min(g[0], g[2]) - g[4], min(g[1], g[3]) - g[4], max(g[0], g[2]) + g[4], max(g[1], g[3]) + g[4]
        for i in range(int(x0 // 4), int(x1 // 4) + 1):
            for j in range(int(y0 // 4), int(y1 // 4) + 1):
                cells.setdefault((i, j), []).append(o)

    def clear(x, y):
        m = via_r + gap
        for o in cells.get((int(x // 4), int(y // 4)), []) + cells.get((int((x + 1) // 4), int(y // 4)), []) + \
                cells.get((int((x - 1) // 4), int(y // 4)), []) + cells.get((int(x // 4), int((y + 1) // 4)), []) + \
                cells.get((int(x // 4), int((y - 1) // 4)), []):
            kind, g = o
            if kind == "box":
                dx = max(g[0] - x, 0, x - g[2])
                dy = max(g[1] - y, 0, y - g[3])
                if math.hypot(dx, dy) < m:
                    return False
            elif kind == "circle":
                if math.hypot(g[0] - x, g[1] - y) < m + g[2]:
                    return False
            else:
                x0, y0, x1, y1, hw = g
                vx, vy = x1 - x0, y1 - y0
                ll = vx * vx + vy * vy
                u = 0 if ll == 0 else max(0, min(1, ((x - x0) * vx + (y - y0) * vy) / ll))
                if math.hypot(x0 + u * vx - x, y0 + u * vy - y) < m + hw:
                    return False
        return True

    added = 0
    y = 1.5
    while y < H - 1.4:
        x = 1.5
        while x < W - 1.4:
            p = pt(x, y)
            if clear(x, y) and not any(z.Outline().Contains(pcbnew.VECTOR2I(p.x, p.y), -1, mm(0.4)) for z in keep):
                v = pcbnew.PCB_VIA(board)
                v.SetPosition(p)
                v.SetWidth(mm(2 * via_r))
                v.SetDrill(mm(0.3))
                v.SetNet(gnd)
                board.Add(v)
                cells.setdefault((int(x // 4), int(y // 4)), []).append(("circle", (x, y, via_r)))
                added += 1
            x += step
        y += step
    return added


def plane(board):
    """L2: the solid GND reference plane (the only copper zone during routing)."""
    _zone(board, pcbnew.In1_Cu, _nets(board)["GND"], FULL, 0, "GND reference plane")


FULL = [(0.3, 0.3), (W - 0.3, 0.3), (W - 0.3, H - 0.3), (0.3, H - 0.3)]


def _zone(board, layer, net, pts, prio=0, name=""):
    z = pcbnew.ZONE(board)
    z.SetLayer(layer)
    z.SetNet(net)
    z.SetAssignedPriority(prio)
    z.SetMinThickness(mm(0.2))
    z.SetLocalClearance(mm(0.25))
    z.SetPadConnection(pcbnew.ZONE_CONNECTION_THERMAL)
    z.SetThermalReliefGap(mm(0.3))
    z.SetThermalReliefSpokeWidth(mm(0.35))
    z.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
    o = z.Outline()
    o.NewOutline()
    for x, y in pts:
        o.Append(mm(OX + x), mm(OY + y))
    if name:
        z.SetZoneName(name)
    board.Add(z)
    return z


def zones(board):
    """After routing: GND pours on L1, L3 and L4; 5V_SYS pour on L3 up the module's right side."""
    nets = _nets(board)
    _zone(board, pcbnew.F_Cu, nets["GND"], FULL, 0, "GND top")
    _zone(board, pcbnew.B_Cu, nets["GND"], FULL, 0, "GND bottom")
    _zone(board, pcbnew.In2_Cu, nets["GND"], FULL, 0, "GND inner")
    # 5V_SYS: from the U103 inductor up the corridor between the module and the RJ45 to its supply pads
    _zone(board, pcbnew.In2_Cu, nets["5V_SYS"], [(58.0, 38.2), (73.4, 38.2), (73.4, 9.0), (70.4, 9.0), (70.4, 37.9),
                                   (70.3, 38.0), (58.0, 38.0)], 1, "5V_SYS")


# ---------------------------------------------------------------------------------------------
def courtyard_boxes(fps):
    out = {}
    for ref, fp in fps.items():
        fp.BuildCourtyardCaches()
        c = fp.GetCourtyard(pcbnew.F_CrtYd)
        if c.OutlineCount():
            bb = c.BBox()
        else:
            bb = fp.GetBoundingBox(False, False)
        out[ref] = (pcbnew.ToMM(bb.GetLeft()) - OX, pcbnew.ToMM(bb.GetTop()) - OY,
                    pcbnew.ToMM(bb.GetRight()) - OX, pcbnew.ToMM(bb.GetBottom()) - OY)
    return out


def check_placement(board, fps):
    """Courtyard overlaps (bounding boxes, 0.05 mm tolerance) and parts outside the board."""
    boxes = courtyard_boxes(fps)
    for i, (x, y) in enumerate(MH, start=1):
        boxes[f"H{i}"] = (x - 2.95, y - 2.95, x + 2.95, y + 2.95)
    for i, (x, y) in enumerate(FIDUCIALS, start=1):
        boxes[f"FID{i}"] = (x - 1.25, y - 1.25, x + 1.25, y + 1.25)
    problems = []
    refs = sorted(boxes)
    for i, a in enumerate(refs):
        ax0, ay0, ax1, ay1 = boxes[a]
        if ax0 < -0.01 or ay0 < -0.01 or ax1 > W + 0.01 or ay1 > H + 0.01:
            problems.append(f"{a} outside the board: {ax0:.2f},{ay0:.2f} .. {ax1:.2f},{ay1:.2f}")
        for b in refs[i + 1:]:
            bx0, by0, bx1, by1 = boxes[b]
            ox = min(ax1, bx1) - max(ax0, bx0)
            oy = min(ay1, by1) - max(ay0, by0)
            if ox > 0.05 and oy > 0.05:
                problems.append(f"{a} overlaps {b} by {ox:.2f} x {oy:.2f} mm")
    return problems


# ---------------------------------------------------------------------------------------------
def strip_nets_for_routing(board, fps=None):
    """Before routing: remove the nets of U201's pads and of the nets left to the LT7911D layout."""
    saved = []
    for fp in board.GetFootprints():
        ref = fp.GetReference()
        for pad in fp.Pads():
            if pad.GetNetCode() <= 0:
                continue
            if ref in UNROUTED_PARTS or UNROUTED_NETS.match(pad.GetNetname()):
                saved.append((pad, pad.GetNet()))
                pad.SetNetCode(0)
    return saved


def fanout_gnd(board):
    """A via to the L2 plane next to every GND pad on the top layer, joined by a short track, so the
    router only has signals and supplies left. Vias keep 0.2 mm from other copper, stay out of the
    board edge margin and never go under the castellated module."""
    via_d, via_drill, gap, tw = 0.55, 0.3, 0.2, 0.3
    gnd = board.GetNetsByName()["GND"]
    items = []            # (x, y, r, netcode) of every pad and via, board mm, as circles/boxes
    boxes = []            # (x0, y0, x1, y1, netcode) of top-layer pads and every drilled pad
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            bb = pad.GetBoundingBox()
            boxes.append((pcbnew.ToMM(bb.GetLeft()) - OX, pcbnew.ToMM(bb.GetTop()) - OY,
                          pcbnew.ToMM(bb.GetRight()) - OX, pcbnew.ToMM(bb.GetBottom()) - OY,
                          pad.GetNetCode(), pad.GetAttribute() == pcbnew.PAD_ATTRIB_PTH))
    module = (MOD[0] - 15.9, MOD[1] - 15.9, MOD[0] + 15.9, MOD[1] + 15.9)
    keepouts = [z for z in board.Zones() if z.GetIsRuleArea() and z.GetDoNotAllowVias()]
    for fp in board.GetFootprints():
        keepouts += [z for z in fp.Zones() if z.GetIsRuleArea() and z.GetDoNotAllowVias()]
    vias = []

    def free(x, y, own_box):
        r = via_d / 2
        if x - r < 0.6 or y - r < 0.6 or x + r > W - 0.6 or y + r > H - 0.6:
            return False
        if module[0] < x < module[2] and module[1] < y < module[3]:
            return False
        p = pt(x, y)
        for z in keepouts:
            if z.Outline().Contains(pcbnew.VECTOR2I(p.x, p.y), -1, mm(r + 0.05)):
                return False
        for x0, y0, x1, y1, net, _ in boxes:
            if net == gnd.GetNetCode() and (x0, y0, x1, y1) == own_box:
                continue
            if x0 - r - gap < x < x1 + r + gap and y0 - r - gap < y < y1 + r + gap:
                return False
        for vx, vy in vias:
            if (vx - x) ** 2 + (vy - y) ** 2 < (via_d + gap) ** 2:
                return False
        for mx, my in MH:
            if (mx - x) ** 2 + (my - y) ** 2 < (3.2 + r) ** 2:
                return False
        return True

    def track_free(x0, y0, x1, y1, own_box):
        n = max(2, int(math.hypot(x1 - x0, y1 - y0) / 0.1))
        m = tw / 2 + gap
        for i in range(n + 1):
            x, y = x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n
            for bx0, by0, bx1, by1, net, _ in boxes:
                if net == gnd.GetNetCode():
                    continue
                if bx0 - m < x < bx1 + m and by0 - m < y < by1 + m:
                    return False
        return True

    added = 0
    for fp in board.GetFootprints():
        ref = fp.GetReference()
        if ref in UNROUTED_PARTS:
            continue
        for pad in fp.Pads():
            if pad.GetNetCode() != gnd.GetNetCode() or pad.GetAttribute() != pcbnew.PAD_ATTRIB_SMD:
                continue
            if not pad.IsOnLayer(pcbnew.F_Cu):
                continue
            bb = pad.GetBoundingBox()
            own = (pcbnew.ToMM(bb.GetLeft()) - OX, pcbnew.ToMM(bb.GetTop()) - OY,
                   pcbnew.ToMM(bb.GetRight()) - OX, pcbnew.ToMM(bb.GetBottom()) - OY)
            px, py = board_xy(pad.GetPosition())
            hw, hh = (own[2] - own[0]) / 2, (own[3] - own[1]) / 2
            if hw > 1.2 and hh > 1.2:          # large exposed pads: vias inside the pad
                if any(x0 >= own[0] and x1 <= own[2] and y0 >= own[1] and y1 <= own[3] and drilled
                       for x0, y0, x1, y1, net, drilled in boxes if net == gnd.GetNetCode()):
                    continue                   # the footprint has its own thermal vias there
                cands = [(px, py)]
            else:
                cx, cy = board_xy(fp.GetPosition())
                ux, uy = px - cx, py - cy
                n = math.hypot(ux, uy) or 1.0
                ux, uy = ux / n, uy / n
                dirs = [(ux, uy), (1, 0), (-1, 0), (0, 1), (0, -1), (0.707, 0.707), (-0.707, 0.707),
                        (0.707, -0.707), (-0.707, -0.707)]
                cands = []
                for step in (0.0, 0.3, 0.6, 1.0, 1.5):
                    for dx, dy in dirs:
                        cands.append((px + dx * (hw + via_d / 2 + gap + step) if dx else px,
                                      py + dy * (hh + via_d / 2 + gap + step) if dy else py))
            for x, y in cands:
                inside = own[0] <= x <= own[2] and own[1] <= y <= own[3]
                if inside or (free(x, y, own) and track_free(px, py, x, y, own)):
                    v = pcbnew.PCB_VIA(board)
                    v.SetPosition(pt(x, y))
                    v.SetWidth(mm(via_d))
                    v.SetDrill(mm(via_drill))
                    v.SetNet(gnd)
                    board.Add(v)
                    vias.append((x, y))
                    if not inside:
                        t = pcbnew.PCB_TRACK(board)
                        t.SetStart(pad.GetPosition())
                        t.SetEnd(pt(x, y))
                        t.SetWidth(mm(tw))
                        t.SetLayer(pcbnew.F_Cu)
                        t.SetNet(gnd)
                        board.Add(t)
                    added += 1
                    break
    return added


def _pt_seg(px, py, ax, ay, bx, by):
    vx, vy = bx - ax, by - ay
    ll = vx * vx + vy * vy
    t = 0.0 if ll == 0 else max(0.0, min(1.0, ((px - ax) * vx + (py - ay) * vy) / ll))
    return math.hypot(px - ax - t * vx, py - ay - t * vy)


def _seg_seg(a, b, c, d):
    def cross(o, p, q):
        return (p[0] - o[0]) * (q[1] - o[1]) - (p[1] - o[1]) * (q[0] - o[0])
    if cross(a, b, c) * cross(a, b, d) < 0 and cross(c, d, a) * cross(c, d, b) < 0:
        return 0.0
    return min(_pt_seg(*a, *c, *d), _pt_seg(*b, *c, *d), _pt_seg(*c, *a, *b), _pt_seg(*d, *a, *b))


def fix_hole_spacing(board, min_gap=0.25, clearance=0.2):
    """Freerouting does not know the hole-to-hole rule between items of one net, so it can drop a
    via next to a drilled pad or another via of the same net. Move each such via by the smallest
    step that clears every hole by `min_gap` and keeps `clearance` from the copper of other nets;
    the ends of its tracks move with it. Returns (moved, [positions that could not be fixed])."""
    holes = []                      # (x0, y0, x1, y1, radius, item): a slot is a segment
    for fp in board.GetFootprints():
        for p in fp.Pads():
            ds = p.GetDrillSize()
            if ds.x <= 0:
                continue
            x, y = board_xy(p.GetPosition())
            dx, dy = pcbnew.ToMM(ds.x), pcbnew.ToMM(ds.y)
            half = abs(dx - dy) / 2
            a = math.radians(p.GetOrientation().AsDegrees())
            ux, uy = (math.cos(a), -math.sin(a)) if dx > dy else (math.sin(a), math.cos(a))
            holes.append((x - ux * half, y - uy * half, x + ux * half, y + uy * half, min(dx, dy) / 2, p))
    tracks = list(board.GetTracks())
    vias = [t for t in tracks if t.GetClass() == "PCB_VIA"]
    for v in vias:
        x, y = board_xy(v.GetPosition())
        holes.append((x, y, x, y, pcbnew.ToMM(v.GetDrillValue()) / 2, v))
    pads = [(p, p.GetEffectivePolygon()) for fp in board.GetFootprints() for p in fp.Pads()]
    keepouts = [z for z in board.Zones() if z.GetIsRuleArea() and z.GetDoNotAllowVias()]
    for fp in board.GetFootprints():
        keepouts += [z for z in fp.Zones() if z.GetIsRuleArea() and z.GetDoNotAllowVias()]

    def gap(x, y, r, h):
        return _pt_seg(x, y, *h[:4]) - r - h[4]

    def copper_ok(v, x, y, ends):
        net, rv = v.GetNetCode(), pcbnew.ToMM(v.GetWidth()) / 2
        if x - rv < 0.25 or y - rv < 0.25 or x + rv > W - 0.25 or y + rv > H - 0.25:
            return False
        c = pt(x, y)
        if any(z.Outline().Contains(c, -1, mm(rv)) for z in keepouts):
            return False
        segs = [((board_xy(t.GetStart()) if moved_end == "end" else board_xy(t.GetEnd())), (x, y),
                 pcbnew.ToMM(t.GetWidth()) / 2, t.GetLayer()) for t, moved_end in ends]
        for p, poly in pads:
            if p.GetNetCode() == net:
                continue
            if poly.Collide(c, mm(rv + clearance)):
                return False
            for a, b, hw, layer in segs:
                if p.IsOnLayer(layer) and poly.Collide(pcbnew.SEG(pt(*a), pt(*b)), mm(hw + clearance)):
                    return False
        for t in tracks:
            if t.GetNetCode() == net:
                continue
            if t.GetClass() == "PCB_VIA":
                ox, oy = board_xy(t.GetPosition())
                r2 = pcbnew.ToMM(t.GetWidth()) / 2
                if math.hypot(ox - x, oy - y) < rv + r2 + clearance:
                    return False
                if any(_pt_seg(ox, oy, *a, *b) < hw + r2 + clearance for a, b, hw, _ in segs):
                    return False
                continue
            a2, b2, w2 = board_xy(t.GetStart()), board_xy(t.GetEnd()), pcbnew.ToMM(t.GetWidth()) / 2
            if _pt_seg(x, y, *a2, *b2) < rv + w2 + clearance:
                return False
            if any(layer == t.GetLayer() and _seg_seg(a, b, a2, b2) < hw + w2 + clearance
                   for a, b, hw, layer in segs):
                return False
        return True

    moved, stuck = 0, []
    for v in vias:
        if v.IsLocked():
            continue
        x, y = board_xy(v.GetPosition())
        r = pcbnew.ToMM(v.GetDrillValue()) / 2
        if all(gap(x, y, r, h) >= min_gap for h in holes if h[5] is not v):
            continue
        pos = v.GetPosition()
        ends = [(t, "end" if t.GetEnd() == pos else "start") for t in tracks
                if t.GetClass() != "PCB_VIA" and t.GetNetCode() == v.GetNetCode() and pos in (t.GetStart(), t.GetEnd())]
        best = None
        for step in [0.05 * i for i in range(1, 13)]:
            for k in range(16):
                nx, ny = x + step * math.cos(k * math.pi / 8), y + step * math.sin(k * math.pi / 8)
                if all(gap(nx, ny, r, h) >= min_gap + 0.005 for h in holes if h[5] is not v) and \
                        copper_ok(v, nx, ny, ends):
                    best = (nx, ny)
                    break
            if best:
                break
        if not best:
            stuck.append((round(x, 2), round(y, 2)))
            continue
        v.SetPosition(pt(*best))
        for t, moved_end in ends:
            (t.SetEnd if moved_end == "end" else t.SetStart)(pt(*best))
        i = next(i for i, h in enumerate(holes) if h[5] is v)
        holes[i] = (best[0], best[1], best[0], best[1], r, v)
        moved += 1
    return moved, stuck


def restore_nets(saved):
    for pad, net in saved:
        pad.SetNet(net)


def autoroute(board, fps, passes: int):
    """Freerouting on everything but U201's pads and the LT7911D nets; the session file comes back
    into the board. Work files go to $ROUTE_DIR (default: a temporary directory)."""
    if not JAR or not os.path.exists(JAR):
        sys.exit("--route needs FREEROUTING_JAR pointing at a Freerouting jar")
    print("GND fan-out vias:", fanout_gnd(board), flush=True)
    saved = strip_nets_for_routing(board, fps)
    work = os.environ.get("ROUTE_DIR") or tempfile.mkdtemp(prefix="box-v1-route-")
    os.makedirs(work, exist_ok=True)
    dsn, ses, log = (os.path.join(work, "box-v1." + e) for e in ("dsn", "ses", "log"))
    if os.path.exists(ses):
        os.remove(ses)
    if not pcbnew.ExportSpecctraDSN(board, dsn):
        sys.exit("DSN export failed")
    text = open(dsn, encoding="utf-8").read()
    m = re.search(r"\(class PWR_3A ([^(]*)", text)
    if not m or "VIN" not in m.group(1).split():
        sys.exit("net classes missing from the DSN export: VIN is not in PWR_3A")
    if "1.9" in os.path.basename(JAR):
        # Freerouting 1.9 always opens its window: give it a virtual display when there is none
        cmd = ["java", "-jar", JAR, "-de", dsn, "-do", ses, "-mp", str(passes)]
        if not os.environ.get("DISPLAY") and shutil.which("xvfb-run"):
            cmd = ["xvfb-run", "-a"] + cmd
    else:
        cmd = ["java", "-jar", JAR, "-de", dsn, "-do", ses, "-mp", str(passes),
               "-mt", str(os.cpu_count() or 2), "--gui.enabled=false"]
    print("routing:", " ".join(cmd), "(log:", log + ")", flush=True)
    with open(log, "w") as f:
        r = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, timeout=4 * 3600)
    if r.returncode != 0 or not os.path.exists(ses):
        sys.exit(f"Freerouting failed (see {log})")
    import_ses(board, ses)
    restore_nets(saved)


def _sexpr(text):
    """Tiny S-expression reader: nested lists of strings."""
    tokens = re.findall(r'"(?:[^"\\]|\\.)*"|\(|\)|[^\s()"]+', text)
    stack, cur = [], []
    for t in tokens:
        if t == "(":
            stack.append(cur)
            cur = []
        elif t == ")":
            done, cur = cur, stack.pop()
            cur.append(done)
        else:
            cur.append(t[1:-1] if t.startswith('"') else t)
    return cur[0]


def import_ses(board, path):
    """Read a Specctra session file (Freerouting's result) into the board: every wire and via of
    network_out replaces the board's tracks. KiCad 7 can only import sessions from its window, so
    this does it directly. Units: resolution um 10 = 0.1 µm; Specctra y points up."""
    tree = _sexpr(open(path, encoding="utf-8").read())
    routes = next(x for x in tree if isinstance(x, list) and x and x[0] == "routes")
    res = next(x for x in routes if isinstance(x, list) and x[0] == "resolution")
    scale = {"um": 1e-3, "mm": 1.0, "mil": 0.0254, "inch": 25.4}[res[1]] / float(res[2])   # -> mm
    layers = {board.GetLayerName(i): i for i in range(pcbnew.PCB_LAYER_ID_COUNT)}
    nets = board.GetNetsByName()
    for t in list(board.GetTracks()):
        if not t.IsLocked():          # fixed tracks (prerouted) do not come back in the session
            board.Remove(t)
    network = next(x for x in routes if isinstance(x, list) and x[0] == "network_out")
    n_wires = n_vias = 0
    for net in network[1:]:
        if not (isinstance(net, list) and net[0] == "net"):
            continue
        ni = nets[net[1]] if net[1] in nets else None
        for item in net[2:]:
            if not isinstance(item, list):
                continue
            if item[0] == "wire":
                path_ = next(x for x in item if isinstance(x, list) and x[0] == "path")
                layer, width = layers[path_[1]], float(path_[2]) * scale
                coords = [float(v) * scale for v in path_[3:] if not isinstance(v, list)]
                ptsl = [(coords[i], -coords[i + 1]) for i in range(0, len(coords) - 1, 2)]
                for (x0, y0), (x1, y1) in zip(ptsl, ptsl[1:]):
                    tr = pcbnew.PCB_TRACK(board)
                    tr.SetStart(pcbnew.VECTOR2I(mm(x0), mm(y0)))
                    tr.SetEnd(pcbnew.VECTOR2I(mm(x1), mm(y1)))
                    tr.SetWidth(mm(width))
                    tr.SetLayer(layer)
                    if ni:
                        tr.SetNet(ni)
                    board.Add(tr)
                    n_wires += 1
            elif item[0] == "via":
                m = re.search(r"_(\d+):(\d+)_um", item[1])
                d, drill = (int(m.group(1)) / 1000, int(m.group(2)) / 1000) if m else (0.55, 0.3)
                v = pcbnew.PCB_VIA(board)
                v.SetPosition(pcbnew.VECTOR2I(mm(float(item[2]) * scale), mm(-float(item[3]) * scale)))
                v.SetWidth(mm(d))
                v.SetDrill(mm(drill))
                if ni:
                    v.SetNet(ni)
                board.Add(v)
                n_vias += 1
    print(f"imported {n_wires} track segments and {n_vias} vias", flush=True)


def save_board(board):
    """Save the board; keep the project file generate.py writes, with pcbnew's board settings."""
    import generate as GEN
    pro = os.path.splitext(OUT)[0] + ".kicad_pro"
    pcbnew.SaveBoard(OUT, board)
    import json
    with open(pro, encoding="utf-8") as f:
        written = json.load(f)
    board_section = written.get("board") or {}
    sev = board_section.setdefault("design_settings", {}).setdefault("rule_severities", {})
    # footprints are embedded in the board file; there is no library table to compare them with
    sev.update({"lib_footprint_issues": "ignore", "lib_footprint_mismatch": "ignore",
                # a pad reached by one thermal spoke is fine for the small passives on the GND pours
                "starved_thermal": "warning"})
    with open(pro, "w", encoding="utf-8") as f:
        json.dump(GEN.kicad_pro(GEN.uid("root"), board_section), f, indent=2)
    prl = os.path.splitext(OUT)[0] + ".kicad_prl"
    if os.path.exists(prl):
        os.remove(prl)
    # SaveBoard renamed the in-memory project to this path and the settings manager keeps it, so a
    # LoadBoard in this process would get it back instead of the merged file (no net class
    # patterns, no severities). Detach the board and drop the project: load the board again after.
    project = board.GetProject()
    board.ClearProject()
    pcbnew.GetSettingsManager().UnloadProject(project, False)


def fill_zones(board):
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())


def drc(board, path):
    pcbnew.WriteDRCReport(board, path, pcbnew.EDA_UNITS_MILLIMETRES, True)
    text = open(path, encoding="utf-8").read()
    counts = {k: int(v) for v, k in re.findall(r"\*\* Found (\d+) (DRC violations|unconnected pads|Footprint errors)", text)}
    return counts, text


def render(path_pcb, outdir):
    """SVG views of the board through kicad-cli: placement (silkscreen, fab, courtyards) and each
    copper layer."""
    os.makedirs(outdir, exist_ok=True)
    views = {
        "pcb-l1": "F.Cu,Edge.Cuts", "pcb-l2": "In1.Cu,Edge.Cuts", "pcb-l3": "In2.Cu,Edge.Cuts",
        "pcb-l4": "B.Cu,Edge.Cuts", "pcb-assembly": "F.SilkS,F.Fab,F.CrtYd,Edge.Cuts",
    }
    for name, layers in views.items():
        out = os.path.join(outdir, name + ".svg")
        subprocess.run(["kicad-cli", "pcb", "export", "svg", "--layers", layers, "--page-size-mode", "2",
                        "--exclude-drawing-sheet", "-o", out, path_pcb], check=True, capture_output=True)
        _compact_svg(out)


def _join_strokes(d):
    """Path data without the moves back to the point the previous segment ended on."""
    out, cur = [], None
    for cmd, args in re.findall(r"([A-Za-z])([^A-Za-z]*)", d):
        nums = args.split()
        if cmd == "M" and cur is not None and nums == cur:
            continue
        out.append(cmd + " ".join(nums))
        cur = nums[-2:] if len(nums) >= 2 else cur
    return " ".join(out)


def _compact_svg(path):
    """Shrink a kicad-cli SVG for the repository: 0.01 mm coordinates, one path per stroked text
    (kicad-cli writes one element per stroke), no line breaks inside path data, no date."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    text = re.sub(r"(\d+\.\d\d)\d+", r"\1", text)
    text = re.sub(r'<g class="stroked-text">.*?</g>',
                  lambda m: m.group(0).replace('"\n/>\n<path d="', " ").replace('" />\n<path d="', " "),
                  text, flags=re.S)
    text = re.sub(r'd="([^"]*)"', lambda m: 'd="' + _join_strokes(m.group(1)) + '"', text)
    # the invisible copies of every text (kept by kicad-cli for search) double the text weight
    text = re.sub(r'<g transform="[^"]*">\s*<text[^>]*opacity="0">[^<]*</text>\s*</g>\n?', "", text)
    text = re.sub(r'<text[^>]*opacity="0">[^<]*</text>\n?', "", text)
    text = re.sub(r" date \d{4}/\d\d/\d\d \d\d:\d\d:\d\d", "", text)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


# ---------------------------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--route", action="store_true", help="route with Freerouting (FREEROUTING_JAR)")
    ap.add_argument("--passes", type=int, default=40, help="Freerouting passes")
    ap.add_argument("--ses", metavar="FILE", help="import this Specctra session instead of routing")
    ap.add_argument("--render", metavar="DIR", help="write SVG views of the board to DIR")
    ap.add_argument("--check-only", action="store_true", help="placement checks only, write nothing")
    args = ap.parse_args()

    design = NL.build()
    board, nets, _ = build_board(design)
    fps, missing, unplaced = place_parts(board, design, nets)
    outline(board)
    problems = check_placement(board, fps)
    print(f"{len(fps)} footprints; {len(missing)} pins without a pad (U201 placeholders); "
          f"{len(unplaced)} unplaced: {', '.join(unplaced) or '-'}")
    for p in problems:
        print("  placement:", p)
    if args.check_only:
        return 1 if problems or unplaced else 0
    rule_areas(board)
    plane(board)
    prerouted(board)
    save_board(board)
    board = pcbnew.LoadBoard(OUT)                # nets and classes as KiCad will see them
    apply_netclasses(board)
    fps = {fp.GetReference(): fp for fp in board.GetFootprints()}
    if args.route:
        autoroute(board, fps, args.passes)
    elif args.ses:
        import_ses(board, args.ses)
    if args.route or args.ses:
        moved, stuck = fix_hole_spacing(board)
        print(f"vias moved off other holes: {moved}; left: {stuck or '-'}", flush=True)
        print("GND stitching vias:", stitch_gnd(board), flush=True)
    zones(board)
    board.BuildConnectivity()
    fill_zones(board)
    save_board(board)
    board = pcbnew.LoadBoard(OUT)                # the rules and severities of the merged project
    counts, _ = drc(board, os.path.join(HERE, "kicad", "drc.rpt"))
    print("DRC:", counts)
    if args.render:
        render(OUT, args.render)
    return 0


if __name__ == "__main__":
    sys.exit(main())
