#!/usr/bin/env python3
"""Generate every derived file of box-v1 from netlist.py (standard library only).

Outputs (relative to this directory):
  kicad/box-v1.kicad_pro, kicad/box-v1.kicad_sch (root) and one sub-sheet per block
  svg/<block>.svg        one legible sheet per block
  bom.csv                grouped bill of materials
  netlist.json           components, pins and nets
  kicad/box-v1.net       KiCad (s-expression, version E) netlist
  svg/mechanical.svg     enclosure outline and connector openings (mechanical.py)
  README.md, ORDERING.md sections between <!-- BEGIN GENERATED: x --> markers are refreshed

Usage:
  python3 generate.py                   # write everything
  python3 generate.py --kicad7 DIR      # also write a KiCad 7 (20230121) copy of the
                                        # schematics to DIR, for kicad-cli 7 ERC/export
"""

from __future__ import annotations

import argparse
import csv
import datetime
import io
import json
import os
import re
import uuid
from collections import OrderedDict, defaultdict

import layout as LY
import mechanical as MECH
import netlist as NL
from netlist import NC, GND, RAILS, BLOCKS, CONFIRMED, LIKELY, UNKNOWN

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = "box-v1"
LIB = "box-v1"
DATE = "2026-09-26"
REV = "v1"
NS = uuid.UUID("6f1c2d3e-4b5a-4c6d-8e9f-0a1b2c3d4e5f")

SHEET_TITLES = {
    "power": "1. Power: USB-C PD input (CH224K), 5V2/5V/3V3/1V2 bucks, iPhone VBUS switch",
    "iphone": "2. iPhone USB-C port + LT7911D (DP Alt Mode -> MIPI CSI-2)",
    "mcu": "3. CH32V305RBT6: USB HS HID to the iPhone, SPI/UART link to the RV1106",
    "soc": "4. Luckfox Core1106 (RV1106G3) + 100M RJ45 + USB-C to the PC",
    "debug": "5. Debug headers, status LEDs, buttons, test points",
}
SHORT_TITLES = {"power": "box-v1: power", "iphone": "box-v1: iPhone USB-C + LT7911D", "mcu": "box-v1: CH32V305",
                "soc": "box-v1: Core1106 + Ethernet + PC USB", "debug": "box-v1: debug, LEDs, buttons"}


def uid(*parts) -> str:
    return str(uuid.uuid5(NS, "/".join(str(p) for p in parts)))


# ---------------------------------------------------------------------------
# S-expression writer
# ---------------------------------------------------------------------------
class Sym(str):
    """A bare (unquoted) token."""


def q(s: str) -> str:
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def num(v: float) -> str:
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def dump(node, indent=0) -> str:
    if isinstance(node, Sym):
        return str(node)
    if isinstance(node, bool):
        return "yes" if node else "no"
    if isinstance(node, (int, float)):
        return num(node)
    if isinstance(node, str):
        return q(node)
    head = node[0]
    simple = all(not isinstance(x, list) for x in node)
    if simple or head in ("at", "xy", "size", "start", "end", "font", "color", "offset", "width",
                          "type", "length", "uuid", "page", "reference", "unit", "path_simple"):
        return "(" + " ".join(dump(x, 0) for x in node) + ")"
    pad = "\t" * (indent + 1)
    parts = [dump(head, 0)]
    inline = []
    i = 1
    while i < len(node) and not isinstance(node[i], list):
        inline.append(dump(node[i], 0))
        i += 1
    s = "(" + " ".join(parts + inline)
    for child in node[i:]:
        if isinstance(child, list) and child and child[0] == "pts":
            s += "\n" + pad + "(pts " + " ".join(dump(c, 0) for c in child[1:]) + ")"
        else:
            s += "\n" + pad + dump(child, indent + 1)
    s += "\n" + "\t" * indent + ")"
    return s


def to_kicad7(node):
    """Down-convert a KiCad 8 tree to the KiCad 7 (20230121) dialect."""
    if not isinstance(node, list):
        return node
    out = []
    for x in node:
        if isinstance(x, list) and x:
            if x[0] in ("generator_version", "exclude_from_sim"):
                continue
            if x[0] in ("hide", "bold", "italic") and len(x) == 2:
                if x[1] == "yes":
                    out.append(Sym(x[0]))
                continue
            if x[0] == "fields_autoplaced":
                out.append([Sym("fields_autoplaced")])
                continue
            if x[0] == "version":
                out.append([Sym("version"), 20230121])
                continue
        out.append(to_kicad7(x))
    return out


def eff(size=1.27, justify=None, hide=False, bold=False):
    font = [Sym("font"), [Sym("size"), size, size]]
    if bold:
        font.append([Sym("bold"), Sym("yes")])
    e = [Sym("effects"), font]
    if justify:
        e.append([Sym("justify")] + [Sym(j) for j in justify.split()])
    if hide:
        e.append([Sym("hide"), Sym("yes")])
    return e


def prop(name, value, x, y, angle=0, hide=False, justify=None, size=1.27):
    return [Sym("property"), name, value, [Sym("at"), x, y, angle], eff(size, justify, hide)]


def stroke(w=0.254):
    return [Sym("stroke"), [Sym("width"), w], [Sym("type"), Sym("default")]]


def fill(t="none"):
    return [Sym("fill"), [Sym("type"), Sym(t)]]


def poly(pts, w=0.254, f="none"):
    return [Sym("polyline"), [Sym("pts")] + [[Sym("xy"), x, y] for x, y in pts], stroke(w), fill(f)]


def rect(x1, y1, x2, y2, w=0.254, f="none"):
    return [Sym("rectangle"), [Sym("start"), x1, y1], [Sym("end"), x2, y2], stroke(w), fill(f)]


def circle(cx, cy, r, w=0.254, f="none"):
    return [Sym("circle"), [Sym("center"), cx, cy], [Sym("radius"), r], stroke(w), fill(f)]


def arc(sx, sy, mx, my, ex, ey, w=0.254):
    return [Sym("arc"), [Sym("start"), sx, sy], [Sym("mid"), mx, my], [Sym("end"), ex, ey], stroke(w), fill()]


KTYPE = {NL.IN: "input", NL.OUT: "output", NL.BIDI: "bidirectional", NL.PWR_IN: "power_in",
         NL.PWR_OUT: "power_out", NL.PASSIVE: "passive", NL.OC: "open_collector",
         NL.NCPIN: "no_connect"}


def kpin(ptype, x, y, angle, length, name, number, hide=False):
    p = [Sym("pin"), Sym(ptype), Sym("line"), [Sym("at"), x, y, angle], [Sym("length"), length]]
    if hide:
        p.append([Sym("hide"), Sym("yes")])
    p += [[Sym("name"), name, eff()], [Sym("number"), number, eff()]]
    return p


def lib_header(name, ref, value, footprint="", desc="", power=False, pin_names_hide=False,
               pin_numbers_hide=False, offset=0.508, ref_at=(0, 2.54), val_at=(0, -2.54),
               ref_just=None, val_just=None):
    s = [Sym("symbol"), f"{LIB}:{name}"]
    if power:
        s.append([Sym("power")])
    if pin_numbers_hide:
        s.append([Sym("pin_numbers"), Sym("hide")])
    pn = [Sym("pin_names"), [Sym("offset"), offset]]
    if pin_names_hide:
        pn.append(Sym("hide"))
    s.append(pn)
    s += [[Sym("exclude_from_sim"), Sym("no")], [Sym("in_bom"), Sym("no" if power else "yes")],
          [Sym("on_board"), Sym("no" if power else "yes")]]
    s.append(prop("Reference", ref, ref_at[0], ref_at[1], hide=power, justify=ref_just))
    s.append(prop("Value", value, val_at[0], val_at[1], justify=val_just))
    s.append(prop("Footprint", footprint, 0, 0, hide=True))
    s.append(prop("Datasheet", "", 0, 0, hide=True))
    s.append(prop("Description", desc, 0, 0, hide=True))
    return s


# ---------------------------------------------------------------------------
# lib symbols
# ---------------------------------------------------------------------------
def two_pin_graphics(style):
    g = []
    if style == "R":
        g.append(rect(-2.032, 0.762, 2.032, -0.762))
    elif style == "C":
        g += [poly([(-0.508, 1.524), (-0.508, -1.524)], 0.3048), poly([(0.508, 1.524), (0.508, -1.524)], 0.3048),
              poly([(-2.54, 0), (-0.508, 0)]), poly([(0.508, 0), (2.54, 0)])]
    elif style == "CP":
        g += [poly([(-0.508, 1.524), (-0.508, -1.524)], 0.3048), arc(1.016, 1.524, 0.508, 0, 1.016, -1.524),
              poly([(-2.54, 0), (-0.508, 0)]), poly([(0.508, 0), (2.54, 0)]),
              poly([(-1.778, 1.016), (-1.016, 1.016)]), poly([(-1.397, 1.397), (-1.397, 0.635)])]
    elif style == "L":
        for i in range(4):
            x0 = -2.032 + i * 1.016
            g.append(arc(x0, 0, x0 + 0.508, 0.508, x0 + 1.016, 0))
        g += [poly([(-2.54, 0), (-2.032, 0)]), poly([(2.032, 0), (2.54, 0)])]
    elif style == "FB":
        g += [rect(-1.524, 0.762, 1.524, -0.762, f="outline"), poly([(-2.54, 0), (-1.524, 0)]),
              poly([(1.524, 0), (2.54, 0)])]
    elif style == "F":
        g += [rect(-2.032, 0.762, 2.032, -0.762), poly([(-2.54, 0), (2.54, 0)])]
    elif style == "SW":
        g += [circle(-1.524, 0, 0.381), circle(1.524, 0, 0.381), poly([(-1.27, 0.508), (1.524, 1.524)]),
              poly([(-2.54, 0), (-1.905, 0)]), poly([(1.905, 0), (2.54, 0)])]
    elif style in ("D", "D_Zener", "D_TVS", "D_Schottky", "LED"):
        # pin 1 = K on the left, pin 2 = A on the right: triangle points left
        g += [poly([(1.27, 1.27), (1.27, -1.27), (-1.27, 0), (1.27, 1.27)], f="none"),
              poly([(-2.54, 0), (2.54, 0)])]
        if style == "D":
            g.append(poly([(-1.27, 1.27), (-1.27, -1.27)]))
        elif style == "D_Zener":
            g.append(poly([(-1.778, 1.27), (-1.27, 1.27), (-1.27, -1.27), (-0.762, -1.27)]))
        elif style == "D_TVS":
            g.append(poly([(-1.778, 1.524), (-1.27, 1.27), (-1.27, -1.27), (-0.762, -1.524)]))
        elif style == "D_Schottky":
            g.append(poly([(-1.778, 0.762), (-1.778, 1.27), (-1.27, 1.27), (-1.27, -1.27),
                           (-0.762, -1.27), (-0.762, -0.762)]))
        elif style == "LED":
            g += [poly([(-1.27, 1.27), (-1.27, -1.27)]),
                  poly([(-0.254, 1.524), (0.508, 2.286)]), poly([(0.508, 1.524), (1.27, 2.286)])]
    return g


def two_pin_lib(style):
    names = ("K", "A") if style.startswith("D") or style == "LED" else ("~", "~")
    s = lib_header(style, "R" if style in ("R",) else {"C": "C", "CP": "C", "L": "L", "FB": "FB", "F": "F",
                   "SW": "SW", "LED": "D"}.get(style, "D"), style, pin_names_hide=True, pin_numbers_hide=True,
                   ref_at=(0, 2.54), val_at=(0, -2.54))
    s.append([Sym("symbol"), f"{style}_0_1"] + two_pin_graphics(style))
    s.append([Sym("symbol"), f"{style}_1_1",
              kpin("passive", -5.08, 0, 0, 2.54, names[0], "1"),
              kpin("passive", 5.08, 0, 180, 2.54, names[1], "2")])
    return s


def tp_lib():
    s = lib_header("TP", "TP", "TP", pin_names_hide=True, pin_numbers_hide=True, ref_at=(0, 2.54),
                   val_at=(0, -2.54))
    s.append([Sym("symbol"), "TP_0_1", circle(0, 0, 0.762)])
    s.append([Sym("symbol"), "TP_1_1", kpin("passive", -2.54, 0, 0, 1.778, "~", "1")])
    return s


def power_lib(name, gnd=False):
    s = lib_header(f"PWR_{name}", "#PWR", name, power=True, pin_names_hide=True, pin_numbers_hide=True,
                   offset=0, ref_at=(0, -3.81), val_at=(0, 3.556 if not gnd else -3.81),
                   desc=f"Power symbol, net {name}")
    if gnd:
        g = [poly([(0, 0), (0, -1.27), (1.27, -1.27), (0, -2.54), (-1.27, -1.27), (0, -1.27)])]
    else:
        g = [poly([(0, 0), (0, 1.27)]), poly([(-1.27, 1.27), (1.27, 1.27)], 0.3048)]
    s.append([Sym("symbol"), f"PWR_{name}_0_1"] + g)
    # pin name = net name: KiCad 7 names the global power net after the (hidden) pin name, KiCad 8
    # after the Value field; keeping both equal gives the same net in both versions.
    s.append([Sym("symbol"), f"PWR_{name}_1_1", kpin("power_in", 0, 0, 90 if not gnd else 270, 0, name, "1")])
    return s


def flag_lib():
    s = lib_header("PWR_FLAG", "#FLG", "PWR_FLAG", power=True, pin_names_hide=True, pin_numbers_hide=True,
                   offset=0, ref_at=(0, 1.905), val_at=(0, 3.81), desc="Marks a net as driven (ERC)")
    s.append([Sym("symbol"), "PWR_FLAG_0_0", kpin("power_out", 0, 0, 90, 0, "~", "1")])
    s.append([Sym("symbol"), "PWR_FLAG_0_1",
              poly([(0, 0), (0, 1.27), (-1.016, 1.905), (0, 2.54), (1.016, 1.905), (0, 1.27)])])
    return s


def box_lib(name, part, sg):
    w, h = sg.w, sg.h
    s = lib_header(name, part.ref.rstrip("0123456789"), part.value, footprint=part.footprint, desc=part.desc,
                   ref_at=(-w / 2, h / 2 + 3.81), val_at=(-w / 2, h / 2 + 1.27), ref_just="left bottom",
                   val_just="left bottom")
    s.append([Sym("symbol"), f"{name}_0_1", rect(-w / 2, h / 2, w / 2, -h / 2, f="background")])
    pins = []
    for p in part.pins:
        pg = sg.pins[p.num]
        angle = {"L": 0, "R": 180, "B": 90}[pg.side]
        pins.append(kpin(KTYPE[p.kind], pg.x, -pg.y, angle, sg.pin_len, p.name, p.num))
    s.append([Sym("symbol"), f"{name}_1_1"] + pins)
    return s


def box_lib_name(part):
    sig = part.value + "|" + ";".join(f"{p.num}:{p.name}:{p.kind}:{p.side}" for p in part.pins)
    base = re.sub(r"[^A-Za-z0-9_.-]+", "_", part.value).strip("_")
    return f"{base}_{uuid.uuid5(NS, sig).hex[:6]}"


# ---------------------------------------------------------------------------
# KiCad schematic
# ---------------------------------------------------------------------------
PAPERS = [("A4", 297, 210), ("A3", 420, 297), ("A2", 594, 420), ("A1", 841, 594), ("A0", 1189, 841)]


def choose_paper(block, parts):
    for name, w, h in PAPERS:
        sl = LY.layout_sheet(block, parts, LY.KICAD, w - 20)
        if sl.width <= w - 10 and sl.height <= h - 45:
            return name, w, h, sl
    name, w, h = PAPERS[-1]
    return name, w, h, LY.layout_sheet(block, parts, LY.KICAD, w - 20)


def rail_rotation(side, gnd):
    if gnd:
        return {"L": 270, "R": 90, "B": 0}[side]
    return {"L": 90, "R": 270, "B": 180}[side]


class PwrCounter:
    def __init__(self):
        self.n = 0

    def next(self, prefix="#PWR"):
        self.n += 1
        return f"{prefix}{self.n:04d}"


def title_block(title, sheet_no, total):
    return [Sym("title_block"), [Sym("title"), title], [Sym("date"), DATE], [Sym("rev"), REV],
            [Sym("company"), "iphone-hid / hardware/box-v1"],
            [Sym("comment"), 1, "Generated from netlist.py by generate.py; edit netlist.py, not this file"],
            [Sym("comment"), 2, "Pin numbers '?..' = [Unknown], must be confirmed before layout"],
            [Sym("comment"), 3, f"Sheet {sheet_no}/{total}"]]


def symbol_instance(lib_id, ref, value, x, y, angle, path, pins, footprint="", desc="", fields=(),
                    dnp=False, in_bom=True, ref_at=None, val_at=None, hide_ref=False, val_just=None,
                    ref_just=None, uuid_key=None, val_angle=0, hide_val=False):
    s = [Sym("symbol"), [Sym("lib_id"), lib_id], [Sym("at"), x, y, angle], [Sym("unit"), 1],
         [Sym("exclude_from_sim"), Sym("no")], [Sym("in_bom"), Sym("yes" if in_bom else "no")],
         [Sym("on_board"), Sym("yes" if in_bom else "no")], [Sym("dnp"), Sym("yes" if dnp else "no")],
         [Sym("uuid"), uid("sym", uuid_key or ref)]]
    rx, ry = ref_at or (x, y - 3)
    vx, vy = val_at or (x, y + 3)
    s.append(prop("Reference", ref, rx, ry, hide=hide_ref, justify=ref_just))
    s.append(prop("Value", value, vx, vy, angle=val_angle, justify=val_just, hide=hide_val))
    s.append(prop("Footprint", footprint, x, y, hide=True))
    s.append(prop("Datasheet", "", x, y, hide=True))
    s.append(prop("Description", desc, x, y, hide=True))
    for k, v in fields:
        s.append(prop(k, v, x, y, hide=True))
    for pn in pins:
        s.append([Sym("pin"), pn, [Sym("uuid"), uid("pin", uuid_key or ref, pn)]])
    s.append([Sym("instances"), [Sym("project"), PROJECT, [Sym("path"), path, [Sym("reference"), ref],
                                                           [Sym("unit"), 1]]]])
    return s


def global_label(net, x, y, side, key):
    angle = {"L": 180, "R": 0}[side]
    just = "right" if side == "L" else "left"
    return [Sym("global_label"), net, [Sym("shape"), Sym("passive")], [Sym("at"), x, y, angle],
            [Sym("fields_autoplaced"), Sym("yes")], eff(1.27, just), [Sym("uuid"), uid("gl", key)],
            [Sym("property"), "Intersheetrefs", "${INTERSHEET_REFS}", [Sym("at"), x, y, 0],
             eff(1.27, just, hide=True)]]


def text_item(txt, x, y, size=1.27, key="", bold=False, justify="left bottom"):
    return [Sym("text"), txt, [Sym("exclude_from_sim"), Sym("no")], [Sym("at"), x, y, 0],
            eff(size, justify, bold=bold), [Sym("uuid"), uid("txt", key or txt)]]


def build_kicad(design):
    root_uuid = uid("root")
    blocks = list(BLOCKS)
    lib_syms = OrderedDict()
    sheets = {}
    pwr = PwrCounter()
    flg = PwrCounter()
    net_pins = design.net_pins()

    def need_flag(net):
        kinds = {p.kind for part, p in net_pins.get(net, [])}
        return NL.PWR_IN in kinds and NL.PWR_OUT not in kinds

    placements = {}
    for bi, block in enumerate(blocks):
        parts = [p for p in design.parts if p.block == block]
        paper, pw, ph, sl = choose_paper(block, parts)
        placements[block] = (paper, pw, ph, sl)

    for style in sorted({p.symbol for p in design.parts if LY.is_two_pin(p)}):
        lib_syms[style] = two_pin_lib(style)
    lib_syms["TP"] = tp_lib()
    for rail in RAILS:
        lib_syms[f"PWR_{rail}"] = power_lib(rail, gnd=(rail == GND))
    lib_syms["PWR_FLAG"] = flag_lib()

    for bi, block in enumerate(blocks):
        paper, pw, ph, sl = placements[block]
        sheet_uuid = uid("sheet", block)
        path = f"/{root_uuid}/{sheet_uuid}"
        items = []
        used_libs = set()
        ox, oy = 10.16, 20.32
        items.append(text_item(SHEET_TITLES[block], ox, oy - 7.62, 2.54, key=block + "title", bold=True))
        items.append(text_item("Net labels sit on the pin ends (no wires). Pin numbers '?..' = no datasheet yet, "
                               "must be confirmed before footprint/layout work.", ox, oy - 3.81, 1.27,
                               key=block + "note"))
        for pl in sl.items:
            part = pl.part
            x0, y0 = pl.x + ox, pl.y + oy
            sg = pl.sym
            if sg.kind == "box":
                name = box_lib_name(part)
                if name not in lib_syms:
                    lib_syms[name] = box_lib(name, part, LY.sym_geom(part, LY.KICAD))
                lib_id = f"{LIB}:{name}"
                ref_at = (x0 - sg.w / 2, y0 - sg.h / 2 - 3.81)
                val_at = (x0 - sg.w / 2, y0 - sg.h / 2 - 1.27)
                rj = vj = "left bottom"
            elif sg.kind == "two":
                lib_id = f"{LIB}:{part.symbol}"
                ref_at, val_at = (x0, y0 - 2.54), (x0, y0 + 2.54)
                rj = vj = None
            else:
                lib_id = f"{LIB}:TP"
                ref_at, val_at = (x0 + 1.27, y0 - 1.27), (x0 + 1.27, y0 + 2.54)
                rj = vj = "left"
            used_libs.add(lib_id)
            fields = [("MPN", part.mpn), ("Manufacturer", part.manufacturer), ("LCSC", part.lcsc),
                      ("Confidence", part.conf)]
            if part.note:
                fields.append(("Note", part.note))
            items.append(symbol_instance(lib_id, part.ref, part.value, x0, y0, 0, path,
                                         [p.num for p in part.pins], part.footprint, part.desc, fields,
                                         dnp=part.dnp, ref_at=ref_at, val_at=val_at, ref_just=rj, val_just=vj))
            for p in part.pins:
                pg = sg.pins[p.num]
                px, py = x0 + pg.x, y0 + pg.y
                key = f"{part.ref}.{p.num}"
                if p.net == NC:
                    items.append([Sym("no_connect"), [Sym("at"), px, py], [Sym("uuid"), uid("nc", key)]])
                elif p.net in RAILS:
                    gnd = p.net == GND
                    rot = rail_rotation(pg.side, gnd)
                    ref = pwr.next()
                    # A rotated symbol rotates (and may mirror the justification of) its Value field, so
                    # on the left/right sides the field is hidden and the rail name is a plain text item.
                    side_txt = pg.side in ("L", "R")
                    items.append(symbol_instance(f"{LIB}:PWR_{p.net}", ref, p.net, px, py, rot, path, ["1"],
                                                 in_bom=False, ref_at=(px, py), val_at=(px, py + 4.445),
                                                 hide_ref=True, hide_val=side_txt, uuid_key=key + "pwr"))
                    if side_txt:
                        dx = -4.445 if pg.side == "L" else 4.445
                        items.append(text_item(p.net, px + dx, py, 1.27, key=key + "rt",
                                               justify="right" if pg.side == "L" else "left"))
                else:
                    assert pg.side in ("L", "R"), key
                    items.append(global_label(p.net, px, py, pg.side, key))
        # PWR_FLAGs for rails whose power pins are not driven by a power output (ERC)
        if block == "power":
            fx, fy = ox + 5.08, ph - 45.72
            items.append(text_item("PWR_FLAG (ERC): rails fed through an inductor/resistor/connector", fx - 2.54,
                                   fy - 7.62, 1.27, key="flags"))
            for net in RAILS:
                if not need_flag(net):
                    continue
                items.append(symbol_instance(f"{LIB}:PWR_FLAG", flg.next("#FLG"), "PWR_FLAG", fx, fy, 0, path,
                                             ["1"], in_bom=False, ref_at=(fx, fy - 1.905), hide_ref=True,
                                             val_at=(fx, fy - 4.445), uuid_key="flag" + net))
                items.append(symbol_instance(f"{LIB}:PWR_{net}", pwr.next(), net, fx, fy,
                                             0 if net != GND else 0, path, ["1"], in_bom=False,
                                             ref_at=(fx, fy), hide_ref=True,
                                             val_at=(fx + 1.27, fy + (4.445 if net == GND else -2.54)),
                                             val_just="left", uuid_key="flagpwr" + net))
                fx += 20.32
        sheets[block] = (paper, sheet_uuid, items)

    # sub-sheet files
    files = {}
    total = len(blocks) + 1
    for bi, block in enumerate(blocks):
        paper, sheet_uuid, items = sheets[block]
        used = {it[1][1] for it in items if it[0] == "symbol"}
        libs = [lib_syms[k.split(":", 1)[1]] for k in sorted(used)]
        tree = [Sym("kicad_sch"), [Sym("version"), 20231120], [Sym("generator"), "eeschema"],
                [Sym("generator_version"), "8.0"], [Sym("uuid"), uid("file", block)],
                [Sym("paper"), paper], title_block(SHORT_TITLES[block], bi + 2, total),
                [Sym("lib_symbols")] + libs] + items
        files[f"{block}.kicad_sch"] = tree

    # root sheet
    ritems = []
    ritems.append(text_item("iPhone control box v1 (Option B): LT7911D + CH32V305RBT6 + Luckfox Core1106",
                            20.32, 25.4, 3.0, key="rt", bold=True))
    ritems.append(text_item("Sub-sheets connect through global labels and power symbols of the same name. "
                            "See hardware/box-v1/README.md and ORDERING.md.", 20.32, 33.02, 1.5, key="rt2"))
    for bi, block in enumerate(blocks):
        x, y = 20.32 + (bi % 3) * 90, 50.8 + (bi // 3) * 50.8
        su = uid("sheet", block)
        ritems.append([Sym("sheet"), [Sym("at"), x, y], [Sym("size"), 76.2, 30.48],
                       [Sym("fields_autoplaced"), Sym("yes")],
                       [Sym("stroke"), [Sym("width"), 0.1524], [Sym("type"), Sym("solid")]],
                       [Sym("fill"), [Sym("color"), 0, 0, 0, 0.0]], [Sym("uuid"), su],
                       prop("Sheetname", block, x, y - 0.7112, justify="left bottom"),
                       prop("Sheetfile", f"{block}.kicad_sch", x, y + 31.0288, justify="left top"),
                       [Sym("instances"), [Sym("project"), PROJECT, [Sym("path"), f"/{root_uuid}",
                                                                     [Sym("page"), str(bi + 2)]]]]])
        ritems.append(text_item(SHEET_TITLES[block], x + 1.27, y + 15.24, 1.27, key="rs" + block))
    root = [Sym("kicad_sch"), [Sym("version"), 20231120], [Sym("generator"), "eeschema"],
            [Sym("generator_version"), "8.0"], [Sym("uuid"), root_uuid], [Sym("paper"), "A4"],
            title_block("box-v1: top sheet", 1, total), [Sym("lib_symbols")]] + ritems + [
        [Sym("sheet_instances"), [Sym("path"), "/", [Sym("page"), "1"]]]]
    files[f"{PROJECT}.kicad_sch"] = root
    return files, root_uuid


def kicad_pro(root_uuid):
    def nc(name, width, gap=0.0, dp_w=0.0, clearance=0.15):
        return {"name": name, "bus_width": 12, "clearance": clearance, "diff_pair_gap": gap or 0.25,
                "diff_pair_via_gap": 0.25, "diff_pair_width": dp_w or 0.2, "line_style": 0,
                "microvia_diameter": 0.3, "microvia_drill": 0.1, "pcb_color": "rgba(0, 0, 0, 0.000)",
                "schematic_color": "rgba(0, 0, 0, 0.000)", "track_width": width, "via_diameter": 0.45,
                "via_drill": 0.2, "wire_width": 6}
    return {
        "meta": {"filename": f"{PROJECT}.kicad_pro", "version": 1},
        "boards": [],
        "libraries": {"pinned_footprint_libs": [], "pinned_symbol_libs": []},
        "net_settings": {
            # widths for JLC04161H-7628 (L1-L2 prepreg 0.21 mm): estimates, re-check with the fab calculator
            "classes": [nc("Default", 0.2), nc("DIFF_100R", 0.2, 0.15, 0.2),
                        nc("USB_90R", 0.25, 0.15, 0.25), nc("PWR_3A", 1.0, clearance=0.2),
                        nc("PWR_1A", 0.5)],
            "meta": {"version": 3},
            "netclass_patterns": (
                [{"netclass": "DIFF_100R", "pattern": p} for p in
                 ("SS_*", "CSI_*", "LT_AUX_*", "PHONE_SBU*", "ETH_*")] +
                [{"netclass": "USB_90R", "pattern": p} for p in ("PHONE_USB_D*", "PC_USB_D*", "MCU_FS_D*")] +
                [{"netclass": "PWR_3A", "pattern": p} for p in
                 ("VBUS_IN", "VIN", "5V2_PHONE", "PSW_*", "PHONE_VBUS", "5V_SYS", "U102_SW", "U103_SW")] +
                [{"netclass": "PWR_1A", "pattern": p} for p in ("3V3", "1V2", "3V3_LT", "1V2_LT_A",
                                                                "U104_SW", "U105_SW", "PC_VBUS")])},
        "schematic": {"drawing": {"default_line_thickness": 6.0, "default_text_size": 50.0},
                      "legacy_lib_dir": "", "legacy_lib_list": []},
        "sheets": [[root_uuid, "Root"]] + [[uid("sheet", b), b] for b in BLOCKS],
        "text_variables": {},
    }


# ---------------------------------------------------------------------------
# SVG
# ---------------------------------------------------------------------------
C_TEXT, C_BOX, C_BOXF, C_PIN = "#1f2937", "#7c2d12", "#fffbeb", "#374151"
C_NET, C_RAIL, C_GND, C_DIFF, C_NC = "#1d4ed8", "#b91c1c", "#15803d", "#7e22ce", "#9ca3af"
C_UNK, C_MAYBE = "#d97706", "#0e7490"


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


def svg_text(x, y, s, size=13, anchor="start", color=C_TEXT, weight="normal", italic=False, extra=""):
    st = ' font-style="italic"' if italic else ""
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" text-anchor="{anchor}" fill="{color}" '
            f'font-weight="{weight}"{st}{extra}>{esc(s)}</text>')


def net_color(net, design):
    if net == GND:
        return C_GND
    if net in RAILS:
        return C_RAIL
    info = design.nets.get(net)
    if info and info.cls == "diff":
        return C_DIFF
    return C_NET


def svg_label(x, y, net, side, design):
    """Net label anchored at a pin end; side L = extends to the left."""
    g = LY.SVG
    if net == NC:
        d = 5
        return (f'<path d="M{x-d},{y-d} L{x+d},{y+d} M{x-d},{y+d} L{x+d},{y-d}" stroke="{C_NC}" '
                f'stroke-width="2"/>')
    col = net_color(net, design)
    tw = len(net) * g.char_w * (1.12 if net in RAILS else 1.0) + 10
    h = 16
    if side == "L":
        x2 = x - 6 - tw
        path = f"M{x},{y} L{x-6},{y-h/2} L{x2},{y-h/2} L{x2},{y+h/2} L{x-6},{y+h/2} Z"
        tx, anc = x - 8, "end"
    elif side == "R":
        x2 = x + 6 + tw
        path = f"M{x},{y} L{x+6},{y-h/2} L{x2},{y-h/2} L{x2},{y+h/2} L{x+6},{y+h/2} Z"
        tx, anc = x + 8, "start"
    else:  # bottom
        path = f"M{x},{y} L{x-tw/2},{y+6} L{x-tw/2},{y+6+h} L{x+tw/2},{y+6+h} L{x+tw/2},{y+6} Z"
        return (f'<path d="{path}" fill="white" stroke="{col}" stroke-width="1.2"/>' +
                svg_text(x, y + 6 + h - 4, net, 11.5, "middle", col))
    weight = "bold" if net in RAILS else "normal"
    return (f'<path d="{path}" fill="white" stroke="{col}" stroke-width="1.2"/>' +
            svg_text(tx, y + 4.2, net, 11.5, anc, col, weight))


def svg_two_pin(x, y, part):
    s = part.symbol
    out = []
    L = LY.SVG.pitch * 2
    body = 16
    stroke = f'stroke="{C_PIN}" stroke-width="1.6" fill="none"'
    dash = ' stroke-dasharray="4,3"' if part.dnp else ""
    out.append(f'<line x1="{x-L}" y1="{y}" x2="{x-body}" y2="{y}" {stroke}/>')
    out.append(f'<line x1="{x+body}" y1="{y}" x2="{x+L}" y2="{y}" {stroke}/>')
    if s in ("R", "F"):
        out.append(f'<rect x="{x-body}" y="{y-6}" width="{2*body}" height="12" {stroke}{dash}/>')
        if s == "F":
            out.append(f'<line x1="{x-body}" y1="{y}" x2="{x+body}" y2="{y}" {stroke}/>')
    elif s in ("C", "CP"):
        out.append(f'<line x1="{x-body}" y1="{y}" x2="{x-4}" y2="{y}" {stroke}/>')
        out.append(f'<line x1="{x+4}" y1="{y}" x2="{x+body}" y2="{y}" {stroke}/>')
        out.append(f'<line x1="{x-4}" y1="{y-10}" x2="{x-4}" y2="{y+10}" stroke="{C_PIN}" stroke-width="2.4"{dash}/>')
        if s == "CP":
            out.append(f'<path d="M{x+7},{y-10} Q{x+2},{y} {x+7},{y+10}" stroke="{C_PIN}" stroke-width="2.4" fill="none"/>')
            out.append(svg_text(x - 12, y - 8, "+", 11))
        else:
            out.append(f'<line x1="{x+4}" y1="{y-10}" x2="{x+4}" y2="{y+10}" stroke="{C_PIN}" stroke-width="2.4"{dash}/>')
    elif s == "L":
        d = f"M{x-body},{y}" + "".join(f" a4,4 0 0 1 8,0" for _ in range(4))
        out.append(f'<path d="{d}" {stroke}/>')
    elif s == "FB":
        out.append(f'<rect x="{x-12}" y="{y-6}" width="24" height="12" fill="{C_PIN}" stroke="{C_PIN}"/>')
        out.append(f'<line x1="{x-body}" y1="{y}" x2="{x-12}" y2="{y}" {stroke}/>')
        out.append(f'<line x1="{x+12}" y1="{y}" x2="{x+body}" y2="{y}" {stroke}/>')
    elif s == "SW":
        out.append(f'<circle cx="{x-10}" cy="{y}" r="3" {stroke}/><circle cx="{x+10}" cy="{y}" r="3" {stroke}/>')
        out.append(f'<line x1="{x-8}" y1="{y-4}" x2="{x+12}" y2="{y-12}" {stroke}/>')
        out.append(f'<line x1="{x-body}" y1="{y}" x2="{x-13}" y2="{y}" {stroke}/>')
        out.append(f'<line x1="{x+13}" y1="{y}" x2="{x+body}" y2="{y}" {stroke}/>')
    else:  # diodes: K (pin 1) left
        out.append(f'<line x1="{x-body}" y1="{y}" x2="{x+body}" y2="{y}" {stroke}/>')
        out.append(f'<path d="M{x+8},{y-9} L{x+8},{y+9} L{x-8},{y} Z" fill="white" stroke="{C_PIN}" stroke-width="1.6"{dash}/>')
        bar = f"M{x-8},{y-9} L{x-8},{y+9}"
        if s == "D_Zener":
            bar = f"M{x-12},{y-9} L{x-8},{y-9} L{x-8},{y+9} L{x-4},{y+9}"
        elif s == "D_TVS":
            bar = f"M{x-12},{y-12} L{x-8},{y-9} L{x-8},{y+9} L{x-4},{y+12}"
        elif s == "D_Schottky":
            bar = f"M{x-12},{y-5} L{x-12},{y-9} L{x-8},{y-9} L{x-8},{y+9} L{x-4},{y+9} L{x-4},{y+5}"
        out.append(f'<path d="{bar}" stroke="{C_PIN}" stroke-width="2" fill="none"/>')
        if s == "LED":
            out.append(f'<path d="M{x+17},{y-8} l6,-6 M{x+23},{y-8} l6,-6" stroke="{C_PIN}" stroke-width="1.4"/>')
    ref = part.ref + (" (DNP)" if part.dnp else "")
    col = C_UNK if part.conf == UNKNOWN else C_TEXT
    out.append(svg_text(x, y - 15, ref, 12, "middle", col, "bold"))
    out.append(svg_text(x, y + 25, part.value, 11.5, "middle", col))
    return out


def conf_color(conf):
    return {UNKNOWN: C_UNK, LIKELY: C_MAYBE}.get(conf, C_TEXT)


def render_svg(design, block):
    g = LY.SVG
    parts = [p for p in design.parts if p.block == block]
    sl = LY.layout_sheet(block, parts, g, 1500)
    top = 92
    W = max(sl.width, 1100)
    H = sl.height + top + 20
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W:.0f}" height="{H:.0f}" '
           f'viewBox="0 0 {W:.0f} {H:.0f}" font-family="DejaVu Sans, Arial, Helvetica, sans-serif">',
           f'<rect x="0" y="0" width="{W:.0f}" height="{H:.0f}" fill="white"/>']
    out.append(svg_text(20, 34, SHEET_TITLES[block], 22, weight="bold"))
    out.append(svg_text(20, 58, f"iPhone control box {REV} · generated from netlist.py by generate.py · {DATE}", 13,
                        color="#4b5563"))
    lx = 20
    for col, txt in ((C_RAIL, "rail"), (C_GND, "GND"), (C_DIFF, "differential pair"), (C_NET, "signal"),
                     (C_NC, "× = no connect"), (C_MAYBE, "pin [Likely]"), (C_UNK, "pin/value [Unknown] (number '?..')")):
        out.append(f'<rect x="{lx}" y="72" width="12" height="12" fill="{col}"/>')
        out.append(svg_text(lx + 17, 83, txt, 12.5))
        lx += 34 + len(txt) * 7.6
    out.append(f'<g transform="translate(0,{top})">')
    for pl in sl.items:
        part, sg = pl.part, pl.sym
        x0, y0 = pl.x, pl.y
        if sg.kind == "box":
            w, h = sg.w, sg.h
            dash = ' stroke-dasharray="6,4"' if part.dnp else ""
            out.append(f'<rect x="{x0-w/2:.1f}" y="{y0-h/2:.1f}" width="{w:.1f}" height="{h:.1f}" fill="{C_BOXF}" '
                       f'stroke="{C_BOX}" stroke-width="2"{dash}/>')
            out.append(svg_text(x0 - w / 2, y0 - h / 2 - 20, f"{part.ref}  {part.value}", 14.5, weight="bold",
                                color=conf_color(part.conf) if part.conf == UNKNOWN else C_TEXT))
            sub = part.mpn if part.mpn and part.mpn != part.value else ""
            sub = (sub + "  " if sub else "") + (f"LCSC {part.lcsc}" if part.lcsc else "")
            out.append(svg_text(x0 - w / 2, y0 - h / 2 - 5, sub[:70], 11, color="#6b7280"))
            for p in part.pins:
                pg = sg.pins[p.num]
                px, py = x0 + pg.x, y0 + pg.y
                col = conf_color(p.conf)
                if pg.side == "L":
                    ex = x0 - w / 2
                    out.append(f'<line x1="{px:.1f}" y1="{py:.1f}" x2="{ex:.1f}" y2="{py:.1f}" stroke="{C_PIN}" stroke-width="1.4"/>')
                    if p.name != "~":
                        out.append(svg_text(ex + 5, py + 4.5, p.name, 12, "start", col))
                    out.append(svg_text(ex - 3, py - 3, p.num, 10.5, "end", col))
                elif pg.side == "R":
                    ex = x0 + w / 2
                    out.append(f'<line x1="{ex:.1f}" y1="{py:.1f}" x2="{px:.1f}" y2="{py:.1f}" stroke="{C_PIN}" stroke-width="1.4"/>')
                    if p.name != "~":
                        out.append(svg_text(ex - 5, py + 4.5, p.name, 12, "end", col))
                    out.append(svg_text(ex + 3, py - 3, p.num, 10.5, "start", col))
                else:
                    ey = y0 + h / 2
                    out.append(f'<line x1="{px:.1f}" y1="{ey:.1f}" x2="{px:.1f}" y2="{py:.1f}" stroke="{C_PIN}" stroke-width="1.4"/>')
                    out.append(svg_text(px + 3, ey + 12, p.num, 10.5, "start", col))
                out.append(svg_label(px, py, p.net, pg.side, design))
        elif sg.kind == "two":
            out += svg_two_pin(x0, y0, part)
            for p in part.pins:
                pg = sg.pins[p.num]
                out.append(svg_label(x0 + pg.x, y0 + pg.y, p.net, pg.side, design))
        else:  # test point
            p = part.pins[0]
            pg = sg.pins[p.num]
            out.append(f'<line x1="{x0+pg.x}" y1="{y0}" x2="{x0-5}" y2="{y0}" stroke="{C_PIN}" stroke-width="1.6"/>')
            out.append(f'<circle cx="{x0}" cy="{y0}" r="5" fill="white" stroke="{C_PIN}" stroke-width="1.6"/>')
            out.append(svg_text(x0 + 9, y0 + 4, part.ref, 12, weight="bold"))
            out.append(svg_label(x0 + pg.x, y0, p.net, "L", design))
    out.append("</g></svg>")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# BOM, JSON, KiCad netlist
# ---------------------------------------------------------------------------
BOM_COLUMNS = ["reference", "qty", "value", "manufacturer", "mpn", "lcsc", "lcsc_confidence", "lcsc_evidence",
               "jlc_type", "assembly", "dnp", "unit_price_usd_est", "price_basis", "footprint", "confidence",
               "notes"]
EVIDENCE_SHORT = {"SNAP": "JLCPCB parts list snapshot 2026-04-02", "WEB": "search excerpt 2026-09-26",
                  "ALLOW": "allowance (no price found)", "NONE": ""}
ASSEMBLY_TEXT = {NL.SMT: "SMT", NL.THT: "THT (hand or selective solder)",
                 NL.MODULE: "module (castellated, consigned)", NL.PCB: "none (PCB copper)"}
CONF_RANK = {CONFIRMED: 0, LIKELY: 1, UNKNOWN: 2}
EXTENDED_FEE_USD = 3.0     # JLCPCB per extended-part line (help-article excerpt, Likely)
CHOOSE_AT_ORDER = "LCSC code: choose at order"


def ref_key(ref):
    m = re.match(r"([A-Z#]+)(\d+)", ref)
    return (m.group(1), int(m.group(2))) if m else (ref, 0)


def jlc_text(o):
    if o.assembly == NL.PCB or o.jlc == "n/a":
        return "n/a"
    return o.jlc or "Extended (assumed)"


def bom_rows(design):
    """Group parts that share the orderable part (MPN), footprint and fitting option."""
    groups = OrderedDict()
    for p in sorted(design.parts, key=lambda p: ref_key(p.ref)):
        o = p.order
        k = (p.dnp, o.mpn if o else p.mpn, p.footprint)
        groups.setdefault(k, []).append(p)
    rows = []
    for (dnp, mpn, fp), ps in groups.items():
        o = ps[0].order
        values = list(OrderedDict.fromkeys(x.value for x in ps))
        value = " / ".join(values) if len(values) <= 3 else "test pads" if ps[0].symbol == "TP" else values[0]
        notes = "; ".join(OrderedDict.fromkeys(x.desc for x in ps))
        if len(notes) > 160:
            notes = notes[:157].rsplit(";", 1)[0] + "; …"
        worst = max((x.conf for x in ps), key=lambda c: CONF_RANK[c])
        flags = []
        if dnp:
            flags.append("DNP (not fitted)")
        if worst != CONFIRMED:
            flags.append(f"[{worst}]")
        extra = []
        if o.note:
            extra.append(o.note)
        if not o.lcsc and o.assembly not in (NL.PCB,):
            extra.append(CHOOSE_AT_ORDER)
        text = (" ".join(flags) + " " if flags else "") + notes + ("; " + "; ".join(extra) if extra else "")
        price = "" if o.price is None else f"{o.price:.4f}"
        rows.append([" ".join(x.ref for x in ps), len(ps), value, o.manufacturer, o.mpn, o.lcsc, o.lcsc_conf,
                     EVIDENCE_SHORT[o.ev] if o.lcsc else "", jlc_text(o), ASSEMBLY_TEXT[o.assembly],
                     "yes" if dnp else "no", price, EVIDENCE_SHORT.get(o.price_basis, "") if price else "",
                     fp, worst, text])
    rows.sort(key=lambda r: (r[10] == "yes", r[9].startswith("none"), ref_key(r[0].split()[0])))
    return rows


def write_bom(design, path):
    rows = bom_rows(design)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(BOM_COLUMNS)
        w.writerows(rows)
    return rows


def bom_stats(design):
    rows = bom_rows(design)
    orderable = [r for r in rows if not r[9].startswith("none")]
    st = {
        "lines": len(rows), "orderable": len(orderable),
        "pcb_lines": len(rows) - len(orderable),
        "with_mpn": sum(1 for r in orderable if r[4]),
        "lcsc_confirmed": sum(1 for r in orderable if r[5] and r[6] == CONFIRMED),
        "lcsc_likely": sum(1 for r in orderable if r[5] and r[6] == LIKELY),
        "lcsc_empty": sum(1 for r in orderable if not r[5]),
        "basic_pref": sum(1 for r in orderable if r[8] in ("Basic", "Preferred")),
        "extended": sum(1 for r in orderable if r[8].startswith("Extended")),
        "dnp_lines": sum(1 for r in orderable if r[10] == "yes"),
        "empty_refs": [r[0] for r in orderable if not r[5]],
    }
    return st


def cost_estimate(design):
    fitted = [p for p in design.parts if not p.dnp and p.order and p.order.assembly != NL.PCB]
    listed = sum(p.order.price for p in fitted if p.order.price_basis != "ALLOW")
    allowed = sum(p.order.price for p in fitted if p.order.price_basis == "ALLOW")
    core = sum(p.order.price for p in fitted if p.buy == "CORE1106")
    lt = sum(p.order.price for p in fitted if p.buy == "LT7911D")
    ext_lines = {p.order.mpn for p in fitted if p.order.jlc not in ("Basic", "Preferred", "n/a")
                 and p.order.assembly in (NL.SMT, NL.THT)}
    return {"listed": listed, "allowed": allowed, "parts": listed + allowed, "core": core, "lt": lt,
            "ext_lines": len(ext_lines), "n_fitted": len(fitted)}


def write_json(design, path):
    net_pins = design.net_pins()
    data = {
        "design": "iphone-hid box-v1",
        "generated_by": "generate.py from netlist.py",
        "date": DATE,
        "components": [{
            "ref": p.ref, "value": p.value, "manufacturer": p.manufacturer, "mpn": p.mpn, "lcsc": p.lcsc,
            "footprint": p.footprint, "block": p.block, "dnp": p.dnp, "confidence": p.conf,
            "description": p.desc,
            "order": None if p.order is None else {
                "lcsc_confidence": p.order.lcsc_conf, "lcsc_evidence": p.order.ev if p.order.lcsc else "",
                "jlc_type": jlc_text(p.order), "assembly": p.order.assembly,
                "unit_price_usd_est": p.order.price, "price_basis": p.order.price_basis, "note": p.order.note},
            "pins": [{"num": x.num, "name": x.name, "type": KTYPE[x.kind],
                      "net": None if x.net == NC else x.net, "no_connect": x.net == NC,
                      "confidence": x.conf} for x in p.pins]} for p in design.parts],
        "nets": {n: {"class": design.nets[n].cls,
                     "nodes": [[p.ref, pin.num] for p, pin in net_pins[n]]}
                 for n in sorted(net_pins)},
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def write_kicad_net(design, path):
    net_pins = design.net_pins()
    comps = [Sym("components")]
    for p in sorted(design.parts, key=lambda p: ref_key(p.ref)):
        comps.append([Sym("comp"), [Sym("ref"), p.ref], [Sym("value"), p.value], [Sym("footprint"), p.footprint],
                      [Sym("fields"), [Sym("field"), [Sym("name"), "MPN"], p.mpn],
                       [Sym("field"), [Sym("name"), "Manufacturer"], p.manufacturer],
                       [Sym("field"), [Sym("name"), "LCSC"], p.lcsc],
                       [Sym("field"), [Sym("name"), "DNP"], "yes" if p.dnp else "no"]],
                      [Sym("libsource"), [Sym("lib"), LIB], [Sym("part"), p.symbol if p.symbol != "box" else
                                                               box_lib_name(p)], [Sym("description"), p.desc]],
                      [Sym("sheetpath"), [Sym("names"), f"/{p.block}/"],
                       [Sym("tstamps"), f"/{uid('sheet', p.block)}/"]],
                      [Sym("tstamps"), uid("sym", p.ref)]])
    nets = [Sym("nets")]
    for i, n in enumerate(sorted(net_pins), start=1):
        node = [Sym("net"), [Sym("code"), str(i)], [Sym("name"), n]]
        for part, pin in net_pins[n]:
            node.append([Sym("node"), [Sym("ref"), part.ref], [Sym("pin"), pin.num],
                         [Sym("pinfunction"), pin.name], [Sym("pintype"), KTYPE[pin.kind]]])
        nets.append(node)
    tree = [Sym("export"), [Sym("version"), "E"],
            [Sym("design"), [Sym("source"), f"{PROJECT}.kicad_sch"], [Sym("date"), DATE],
             [Sym("tool"), "generate.py (iphone-hid box-v1)"]], comps, nets]
    with open(path, "w", encoding="utf-8") as f:
        f.write(dump(tree) + "\n")


# ---------------------------------------------------------------------------
# README / ORDERING generated sections
# ---------------------------------------------------------------------------
CONF_TAG = {CONFIRMED: "[Confirmed]", LIKELY: "[Likely]", UNKNOWN: "[Unknown]"}


def md_cell(s):
    return str(s).replace("|", "\\|").replace("\n", " ")


def pin_table(design, part):
    net_pins = design.net_pins()
    src = f"[{part.src}]({NL.SOURCES[part.src][0]})" if part.src else "-"
    order = part.order
    buy = f"{order.manufacturer} {order.mpn}" if order and order.assembly != NL.PCB else "-"
    lines = [f"**{part.ref} {part.value}** ({md_cell(buy)}; footprint `{part.footprint}`; source: {src})", "",
             "| Pin | Name | Net | Connects to | Note | Confidence |", "|---|---|---|---|---|---|"]

    def natkey(x):
        return (x.num.startswith("?"), [(0, int(t), "") if t.isdigit() else (1, 0, t)
                                        for t in re.findall(r"\d+|\D+", x.num)])
    for p in sorted(part.pins, key=natkey):
        if p.net == NC:
            net, peers = "NC", "-"
        else:
            net = p.net
            others = [f"{o.ref}.{op.num}" for o, op in net_pins[p.net] if o is not part]
            if p.net in RAILS and len(others) > 6:
                peers = f"rail ({len(others)} pins)"
            else:
                peers = ", ".join(others[:8]) + (" …" if len(others) > 8 else "")
        lines.append(f"| {md_cell(p.num)} | {md_cell(p.name)} | {md_cell(net)} | {md_cell(peers)} | "
                     f"{md_cell(p.note)} | {CONF_TAG[p.conf]} |")
    return "\n".join(lines)


def passive_table(design, block):
    lines = ["| Ref | Value | Pin 1 | Pin 2 | Footprint | Role |", "|---|---|---|---|---|---|"]
    for p in sorted(design.parts, key=lambda p: ref_key(p.ref)):
        if p.block != block or not (LY.is_two_pin(p) or p.symbol == "TP"):
            continue
        n1 = p.pins[0].net
        n2 = p.pins[1].net if len(p.pins) > 1 else "-"
        tag = " **DNP**" if p.dnp else ""
        conf = f" {CONF_TAG[p.conf]}" if p.conf != CONFIRMED else ""
        lines.append(f"| {p.ref}{tag} | {md_cell(p.value)} | {n1} | {n2} | "
                     f"`{p.footprint.split(':')[-1]}` | {md_cell(p.desc)}{conf} |")
    return "\n".join(lines)


def coverage_table(design):
    st = bom_stats(design)
    return "\n".join([
        "| BOM figure | Count |", "|---|---|",
        f"| Lines in `bom.csv` | {st['lines']} |",
        f"| Lines that are PCB copper only (test pads, solder jumpers: nothing to buy) | {st['pcb_lines']} |",
        f"| Orderable lines | {st['orderable']} (of which DNP: {st['dnp_lines']}) |",
        f"| Orderable lines with a manufacturer part number | {st['with_mpn']} |",
        f"| LCSC code [Confirmed] (read on the LCSC/JLCPCB page itself) | {st['lcsc_confirmed']} |",
        f"| LCSC code [Likely] (JLCPCB parts-list snapshot or search excerpt) | {st['lcsc_likely']} |",
        f"| No LCSC code (choose at order) | {st['lcsc_empty']}: {', '.join(st['empty_refs'])} |",
        f"| Lines in JLCPCB's Basic/Preferred list (no feeder fee) | {st['basic_pref']} |",
        f"| Lines assumed Extended (feeder fee per line) | {st['extended']} |",
    ])


def cost_table(design):
    c = cost_estimate(design)
    fee10 = c["ext_lines"] * EXTENDED_FEE_USD / 10
    fee50 = c["ext_lines"] * EXTENDED_FEE_USD / 50
    pcb = (2.0, 5.0)
    case = (3.0, 8.0)
    lo10 = c["parts"] + fee10 + pcb[0] + case[0]
    hi10 = c["parts"] + fee10 + pcb[1] + case[1]
    lo50 = c["parts"] + fee50 + pcb[0] + case[0]
    hi50 = c["parts"] + fee50 + pcb[1] + case[1]
    return "\n".join([
        "| Item (USD per board, estimate) | 10 boards | 50 boards | Basis |", "|---|---|---|---|",
        f"| Components on `bom.csv`, fitted parts ({c['n_fitted']} pieces) | {c['parts']:.2f} | {c['parts']:.2f} | "
        f"unit prices in `bom.csv`: ${c['listed']:.2f} from listings, ${c['allowed']:.2f} allowances |",
        f"| of which Luckfox Core1106 | {c['core']:.2f} | {c['core']:.2f} | top of the $16.34-26.99 range in search excerpts |",
        f"| of which LT7911D | {c['lt']:.2f} | {c['lt']:.2f} | Global Sources excerpt, chip only |",
        f"| JLCPCB extended-part feeder fee, ${EXTENDED_FEE_USD:.0f} x {c['ext_lines']} lines, spread over the batch | "
        f"{fee10:.2f} | {fee50:.2f} | upper bound: every line outside the Basic/Preferred snapshot counted |",
        f"| 4-layer impedance-controlled PCB | {pcb[0]:.0f}-{pcb[1]:.0f} | {pcb[0]:.0f}-{pcb[1]:.0f} | allowance "
        "from docs/research/custom-box.md §9, not a quote |",
        f"| Enclosure | {case[0]:.0f}-{case[1]:.0f} | {case[0]:.0f}-{case[1]:.0f} | allowance from custom-box.md §9 |",
        f"| **Total without assembly labour** | **{lo10:.0f}-{hi10:.0f}** | **{lo50:.0f}-{hi50:.0f}** | estimate |",
    ])


def readme_sections(design):
    sec = {}
    for block in BLOCKS:
        chunks = []
        for p in sorted(design.parts, key=lambda p: ref_key(p.ref)):
            if p.block == block and not LY.is_two_pin(p) and p.symbol != "TP":
                chunks.append(pin_table(design, p))
        chunks.append("Passives and test points of this block:\n\n" + passive_table(design, block))
        sec[f"pins-{block}"] = "\n\n".join(chunks)
    sec["bom-coverage"] = coverage_table(design)
    sec["cost"] = cost_table(design)
    return sec


def update_markdown(design, path):
    if not os.path.exists(path):
        return False
    text = open(path, encoding="utf-8").read()
    for key, body in readme_sections(design).items():
        pat = re.compile(rf"(<!-- BEGIN GENERATED: {re.escape(key)} -->\n)(.*?)(<!-- END GENERATED: {re.escape(key)} -->)",
                         re.S)
        text = pat.sub(lambda m: m.group(1) + body + "\n" + m.group(3), text)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return True


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kicad7", help="also write a KiCad 7 copy of the schematics into this directory")
    args = ap.parse_args()
    design = NL.build()
    kdir = os.path.join(HERE, "kicad")
    sdir = os.path.join(HERE, "svg")
    os.makedirs(kdir, exist_ok=True)
    os.makedirs(sdir, exist_ok=True)
    files, root_uuid = build_kicad(design)
    for name, tree in files.items():
        with open(os.path.join(kdir, name), "w", encoding="utf-8") as f:
            f.write(dump(tree) + "\n")
    with open(os.path.join(kdir, f"{PROJECT}.kicad_pro"), "w", encoding="utf-8") as f:
        json.dump(kicad_pro(root_uuid), f, indent=2)
        f.write("\n")
    if args.kicad7:
        os.makedirs(args.kicad7, exist_ok=True)
        for name, tree in files.items():
            with open(os.path.join(args.kicad7, name), "w", encoding="utf-8") as f:
                f.write(dump(to_kicad7(tree)) + "\n")
        with open(os.path.join(args.kicad7, f"{PROJECT}.kicad_pro"), "w", encoding="utf-8") as f:
            json.dump(kicad_pro(root_uuid), f, indent=2)
    for block in BLOCKS:
        with open(os.path.join(sdir, f"{block}.svg"), "w", encoding="utf-8") as f:
            f.write(render_svg(design, block))
    MECH.write(os.path.join(sdir, "mechanical.svg"))
    rows = write_bom(design, os.path.join(HERE, "bom.csv"))
    write_json(design, os.path.join(HERE, "netlist.json"))
    write_kicad_net(design, os.path.join(kdir, f"{PROJECT}.net"))
    upd = [name for name in ("README.md", "ORDERING.md") if update_markdown(design, os.path.join(HERE, name))]
    print(f"generated: {len(files)} .kicad_sch, {len(BLOCKS)} svg + mechanical.svg, bom.csv ({len(rows)} lines), "
          f"netlist.json, {PROJECT}.net" + (f", tables in {' and '.join(upd)}" if upd else ""))


if __name__ == "__main__":
    main()
