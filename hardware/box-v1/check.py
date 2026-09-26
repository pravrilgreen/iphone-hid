#!/usr/bin/env python3
"""Validate the box-v1 circuit (netlist.py) and the generated files. Standard library only.

    python3 check.py              # design rules + generated files; exit 1 on any error
    python3 check.py --selftest   # also inject faults and prove that each rule catches them
    python3 check.py -v           # list every warning

Rules on the design (errors):
  R1  unique references, unique pin numbers per part, every part has value + footprint
  R2  every pin is on a net or explicitly no-connect (netlist.NC)
  R3  every net has at least two pins
  R4  every IC power-input pin lists decoupling capacitors that really sit between its net and GND
  R5  no net-name collisions (case / separator insensitive), legal characters only
  R6  differential pairs: both halves exist and touch exactly the same parts
  R7  every net carrying a power-input pin is a declared rail
  R8  every source key used by a part or pin exists in netlist.SOURCES; ICs and connectors cite one
  R9  placeholder pin numbers ('?..') are marked [Unknown]
  R10 electrical sanity: regulator outputs, UVLO thresholds, ADC input ranges, CH224K request
  R11 ordering data: every part resolves to a BUY entry with manufacturer + MPN; LCSC codes are well
      formed and carry an evidence key; a code is never [Confirmed] without the vendor page;
      JLCPCB Basic/Preferred classes come only from the parts-list snapshot; every orderable
      part has a unit price or an explicit allowance
Rules on generated files (errors, skipped with a warning when the files are missing):
  G1  every .kicad_sch parses as one balanced S-expression 'kicad_sch' with version 20231120
  G2  every lib_id used is embedded in lib_symbols; hierarchy and instances are consistent
  G3  connectivity re-derived from the schematic geometry (pin ends, labels, power symbols,
      no-connect flags) equals netlist.py pin for pin
  G4  SVG files are well-formed XML and show every reference of their block; svg/mechanical.svg parses
  G5  bom.csv has the expected columns and lists every part exactly once; every line has an MPN; the
      manufacturer, MPN, LCSC code, DNP flag and price of each line match netlist.py; a line without
      an LCSC code says "choose at order"; netlist.json matches netlist.py
Warnings: nets with fewer than two fitted pins (options with DNP parts), [Unknown]/[Likely] counts.
"""

from __future__ import annotations

import copy
import csv
import json
import math
import os
import re
import sys
import xml.etree.ElementTree as ET

import netlist as NL
from netlist import NC, GND, RAILS, SOURCES, UNKNOWN, LIKELY, CONFIRMED

HERE = os.path.dirname(os.path.abspath(__file__))
IC_PREFIX = ("U",)


class Report:
    def __init__(self):
        self.errors = []
        self.warnings = []
        self.info = []

    def err(self, rule, msg):
        self.errors.append(f"{rule}: {msg}")

    def warn(self, rule, msg):
        self.warnings.append(f"{rule}: {msg}")


# ---------------------------------------------------------------------------
# value parsing for R10
# ---------------------------------------------------------------------------
MULT = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "m": 1e-3, "k": 1e3, "K": 1e3, "M": 1e6, "R": 1.0, "": 1.0}


def parse_value(v: str) -> float:
    m = re.match(r"\s*(\d+(?:\.\d+)?)([pnuµmkKMR]?)(\d*)", v)
    if not m:
        raise ValueError(v)
    base = float(m.group(1))
    if m.group(3):  # 4k7 / 2R2 notation
        base = float(f"{m.group(1)}.{m.group(3)}")
    return base * MULT[m.group(2)]


def between(d, net_a, net_b, prefix):
    """Two-pin parts of a given prefix connected between two nets."""
    out = []
    for p in d.parts:
        if p.ref.startswith(prefix) and len(p.pins) == 2 and not p.dnp:
            nets = {p.pins[0].net, p.pins[1].net}
            if nets == {net_a, net_b}:
                out.append(p)
    return out


def divider_ratio(d, top_net, mid_net, bot_net=GND):
    top = between(d, top_net, mid_net, "R")
    bot = between(d, mid_net, bot_net, "R")
    if len(top) != 1 or len(bot) != 1:
        raise ValueError(f"divider {top_net}/{mid_net}/{bot_net}: {len(top)} top, {len(bot)} bottom")
    rt, rb = parse_value(top[0].value), parse_value(bot[0].value)
    return rb / (rt + rb), top[0].ref, bot[0].ref


# (feedback net, output rail, Vref, expected, tolerance) - Vref from the datasheets listed in README
REGULATORS = [
    ("U102_FB", "5V2_PHONE", 1.0, 5.2, 0.05),
    ("U103_FB", "5V_SYS", 1.0, 5.0, 0.05),
    ("U104_FB", "3V3", 0.6, 3.3, 0.03),
    ("U105_FB", "1V2", 0.6, 1.2, 0.03),
]
# (enable net, source net, threshold, min start voltage, max start voltage)
UVLO = [("U102_EN", "VIN", 1.2, 6.5, 8.0), ("U103_EN", "VIN", 1.2, 4.0, 4.6)]
# (sense net, source net, max source voltage, ADC full scale)
ADC_DIVIDERS = [
    ("PHONE_VBUS_SENSE", "PHONE_VBUS", 20.0, 3.3),
    ("VIN_SENSE", "VIN", 20.0, 1.8),
    ("PC_VBUS_DET", "PC_VBUS", 5.5, 3.6),
]


def check_electrical(d, rep):
    for fb, out, vref, want, tol in REGULATORS:
        try:
            k, rt, rb = divider_ratio(d, out, fb)
            v = vref / k
            if abs(v - want) > tol:
                rep.err("R10", f"{out}: feedback {rt}/{rb} gives {v:.3f} V, expected {want} V")
            else:
                rep.info.append(f"R10 {out}: {rt}/{rb} -> {v:.3f} V")
        except ValueError as e:
            rep.err("R10", str(e))
    for en, src, vth, lo, hi in UVLO:
        try:
            k, rt, rb = divider_ratio(d, src, en)
            v = vth / k
            if not lo <= v <= hi:
                rep.err("R10", f"{en}: UVLO {v:.2f} V outside {lo}-{hi} V")
            else:
                rep.info.append(f"R10 {en}: start at VIN ≈ {v:.2f} V")
        except ValueError as e:
            rep.err("R10", str(e))
    for sense, src, vmax, fs in ADC_DIVIDERS:
        try:
            k, rt, rb = divider_ratio(d, src, sense)
            v = vmax * k
            if v > fs:
                rep.err("R10", f"{sense}: {vmax} V on {src} gives {v:.2f} V > {fs} V ADC range")
            else:
                rep.info.append(f"R10 {sense}: {vmax} V -> {v:.2f} V (≤ {fs} V)")
        except ValueError as e:
            rep.err("R10", str(e))
    cfg = between(d, "CH224_CFG1", GND, "R")
    if len(cfg) != 1 or parse_value(cfg[0].value) != 6.8e3:
        rep.err("R10", "CH224K CFG1 must be 6.8 kΩ to GND (9 V request) in the default build")
    else:
        rep.info.append("R10 CH224K: CFG1 6.8 kΩ -> requests 9 V")
    # LEDs: current 1-5 mA from 3.3 V
    for led in [p for p in d.parts if p.symbol == "LED"]:
        a = led.pins[1].net
        rs = [p for p in d.parts if p.ref.startswith("R") and a in (p.pins[0].net, p.pins[1].net)]
        if len(rs) != 1:
            rep.err("R10", f"{led.ref}: expected one series resistor")
            continue
        i = (3.3 - 2.0) / parse_value(rs[0].value) * 1e3
        if not 1.0 <= i <= 5.0:
            rep.err("R10", f"{led.ref}: {i:.1f} mA out of 1-5 mA")


# ---------------------------------------------------------------------------
# design rules
# ---------------------------------------------------------------------------
def check_design(d, rep):
    refs = {}
    for p in d.parts:
        if p.ref in refs:
            rep.err("R1", f"duplicate reference {p.ref}")
        refs[p.ref] = p
        nums = [x.num for x in p.pins]
        dup = {n for n in nums if nums.count(n) > 1}
        if dup:
            rep.err("R1", f"{p.ref}: duplicate pin numbers {sorted(dup)}")
        if not p.value or not p.footprint:
            rep.err("R1", f"{p.ref}: missing value or footprint")
        if re.match(r"(U|J|Q)\d", p.ref) and not p.mpn:
            rep.err("R1", f"{p.ref}: IC/connector without part number")
        for x in p.pins:
            if x.net is None or x.net == "":
                rep.err("R2", f"{p.ref}.{x.num} ({x.name}) is neither on a net nor marked NC")
    net_pins = d.net_pins()
    for net, pins in sorted(net_pins.items()):
        if len(pins) < 2:
            rep.err("R3", f"net {net} has only {len(pins)} pin ({pins[0][0].ref}.{pins[0][1].num})")
        fitted = [pp for pp in pins if not pp[0].dnp]
        if len(pins) >= 2 and len(fitted) < 2:
            rep.warn("W1", f"net {net}: {len(fitted)} fitted pin(s) (option via DNP "
                           f"{', '.join(pp[0].ref for pp in pins if pp[0].dnp)})")
    # R4 decoupling
    shared = {}
    for p in d.parts:
        if not p.ref.startswith(IC_PREFIX):
            continue
        for x in p.pins:
            if x.kind != NL.PWR_IN or x.net in (NC, GND):
                continue
            if not x.decap:
                rep.err("R4", f"{p.ref}.{x.num} ({x.name}, {x.net}) has no decoupling capacitor")
                continue
            for cref in x.decap:
                c = refs.get(cref)
                if c is None or c.symbol not in ("C", "CP"):
                    rep.err("R4", f"{p.ref}.{x.num}: decap {cref} is not a capacitor in the design")
                    continue
                if {c.pins[0].net, c.pins[1].net} != {x.net, GND}:
                    rep.err("R4", f"{p.ref}.{x.num}: {cref} is on {c.pins[0].net}/{c.pins[1].net}, "
                                  f"not {x.net}/GND")
                if c.dnp:
                    rep.err("R4", f"{p.ref}.{x.num}: decap {cref} is DNP")
                shared.setdefault(cref, []).append(f"{p.ref}.{x.num}")
    for cref, users in shared.items():
        if len(users) > 3:
            rep.err("R4", f"{cref} decouples {len(users)} pins ({', '.join(users)}); max 3")
    # R5 net names
    norm = {}
    for net in net_pins:
        if not re.fullmatch(r"[A-Z0-9_+]+", net):
            rep.err("R5", f"net name {net!r} has illegal characters")
        key = re.sub(r"[^A-Z0-9]", "", net.upper())
        if key in norm and norm[key] != net:
            rep.err("R5", f"net names collide: {norm[key]!r} vs {net!r}")
        norm[key] = net
        if net in refs:
            rep.err("R5", f"net {net} equals a reference designator")
    for rail in RAILS:
        key = re.sub(r"[^A-Z0-9]", "", rail.upper())
        if key in norm and norm[key] != rail:
            rep.err("R5", f"net {norm[key]!r} collides with rail {rail!r}")
    # R6 differential pairs
    for pn, nn, z, _ in NL.DIFF:
        if pn not in net_pins or nn not in net_pins:
            rep.err("R6", f"pair {pn}/{nn}: missing net")
            continue
        def sig(net):
            multi = sorted({pp[0].ref for pp in net_pins[net] if len(pp[0].pins) > 2})
            two = sorted((pp[0].symbol, pp[0].value, pp[0].dnp) for pp in net_pins[net] if len(pp[0].pins) <= 2)
            return multi, two
        if sig(pn) != sig(nn):
            rep.err("R6", f"pair {pn}/{nn} is not symmetric: {sig(pn)} vs {sig(nn)}")
    # R7 rails
    for net, pins in net_pins.items():
        if any(x.kind == NL.PWR_IN for _, x in pins) and net not in RAILS:
            rep.err("R7", f"net {net} feeds power pins but is not a declared rail")
    # R8 sources
    for p in d.parts:
        keys = [p.src] + [x.src for x in p.pins]
        for k in keys:
            if k and k not in SOURCES:
                rep.err("R8", f"{p.ref}: unknown source key {k!r}")
        if (p.ref.startswith("U") or p.footprint.startswith(("Connector_USB", "Connector_RJ"))) and not p.src:
            rep.err("R8", f"{p.ref}: IC/connector without a source")
    # R9 placeholders
    for p in d.parts:
        for x in p.pins:
            if x.num.startswith("?") and x.conf != UNKNOWN:
                rep.err("R9", f"{p.ref}.{x.num}: placeholder number must be [Unknown]")
    check_electrical(d, rep)
    check_orders(d, rep)


LCSC_RE = re.compile(r"C\d{3,9}")


def check_orders(d, rep):
    """R11: ordering data of every part."""
    for p in d.parts:
        o = p.order
        if o is None:
            rep.err("R11", f"{p.ref}: BUY key {p.buy!r} not found in netlist.BUY")
            continue
        if not o.manufacturer or not o.mpn:
            rep.err("R11", f"{p.ref}: manufacturer or MPN missing")
        if p.mpn != o.mpn or p.lcsc != o.lcsc:
            rep.err("R11", f"{p.ref}: part MPN/LCSC differ from its BUY entry")
        if o.assembly not in (NL.SMT, NL.THT, NL.MODULE, NL.PCB):
            rep.err("R11", f"{p.ref}: unknown assembly class {o.assembly!r}")
        if o.ev not in NL.EVIDENCE or (o.price_ev and o.price_ev not in NL.EVIDENCE):
            rep.err("R11", f"{p.ref}: unknown evidence key {o.ev!r}/{o.price_ev!r}")
        if o.lcsc:
            if not LCSC_RE.fullmatch(o.lcsc):
                rep.err("R11", f"{p.ref}: malformed LCSC code {o.lcsc!r}")
            if o.ev not in NL.LCSC_CONF:
                rep.err("R11", f"{p.ref}: LCSC code {o.lcsc} without evidence")
            if o.lcsc_conf == CONFIRMED:
                rep.err("R11", f"{p.ref}: LCSC code {o.lcsc} marked Confirmed, but no vendor page was read")
        if o.jlc not in ("", "Basic", "Preferred", "n/a"):
            rep.err("R11", f"{p.ref}: unknown JLCPCB class {o.jlc!r}")
        if o.jlc in ("Basic", "Preferred") and o.ev != "SNAP":
            rep.err("R11", f"{p.ref}: JLCPCB class {o.jlc} claimed without the parts-list snapshot")
        if o.assembly != NL.PCB and (o.price is None or o.price <= 0):
            rep.err("R11", f"{p.ref}: no unit price or allowance")


# ---------------------------------------------------------------------------
# S-expression parser and KiCad checks
# ---------------------------------------------------------------------------
TOKEN = re.compile(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()"]+')


class Str(str):
    """A quoted string token (kept distinct from bare symbols)."""


def parse_sexpr(text: str):
    depth = 0
    stack = [[]]
    for m in TOKEN.finditer(text):
        t = m.group(0)
        if t == "(":
            depth += 1
            stack.append([])
        elif t == ")":
            depth -= 1
            if depth < 0:
                raise ValueError(f"unbalanced ')' at offset {m.start()}")
            node = stack.pop()
            stack[-1].append(node)
        elif t.startswith('"'):
            stack[-1].append(Str(re.sub(r"\\(.)", lambda m: {"n": "\n"}.get(m.group(1), m.group(1)), t[1:-1])))
        else:
            stack[-1].append(t)
    if depth != 0:
        raise ValueError(f"unbalanced: {depth} '(' not closed")
    if len(stack[0]) != 1:
        raise ValueError(f"{len(stack[0])} top-level expressions (expected 1)")
    # leftover non-whitespace outside tokens?
    rest = TOKEN.sub("", text).strip()
    if rest:
        raise ValueError(f"stray characters: {rest[:20]!r}")
    return stack[0][0]


def kids(node, name):
    return [x for x in node if isinstance(x, list) and x and x[0] == name]


def kid(node, name):
    k = kids(node, name)
    return k[0] if k else None


def rot(x, y, angle):
    a = math.radians(angle)
    return x * math.cos(a) - y * math.sin(a), x * math.sin(a) + y * math.cos(a)


def key(x, y):
    return (round(x * 100), round(y * 100))


def lib_pins(sym):
    out = {}

    def walk(n):
        for c in n:
            if isinstance(c, list) and c:
                if c[0] == "pin":
                    at = kid(c, "at")
                    out[kid(c, "number")[1]] = (float(at[1]), float(at[2]), kid(c, "name")[1], c[1])
                else:
                    walk(c)
    walk(sym)
    return out


def check_kicad(d, rep, kdir):
    files = sorted(f for f in os.listdir(kdir) if f.endswith(".kicad_sch"))
    if not files:
        rep.warn("G1", "no .kicad_sch found (run generate.py)")
        return
    trees = {}
    for f in files:
        try:
            t = parse_sexpr(open(os.path.join(kdir, f), encoding="utf-8").read())
        except ValueError as e:
            rep.err("G1", f"{f}: {e}")
            continue
        if t[0] != "kicad_sch":
            rep.err("G1", f"{f}: top token {t[0]!r}")
        v = kid(t, "version")
        if not v or v[1] != "20231120":
            rep.err("G1", f"{f}: version {v and v[1]} (expected 20231120, KiCad 8)")
        for need in ("generator", "generator_version", "uuid", "paper", "lib_symbols"):
            if kid(t, need) is None:
                rep.err("G1", f"{f}: missing ({need})")
        trees[f] = t
    root_name = "box-v1.kicad_sch"
    root = trees.get(root_name)
    if root is None:
        rep.err("G2", "root sheet box-v1.kicad_sch missing")
        return
    root_uuid = kid(root, "uuid")[1]
    sheet_paths = {}
    for sh in kids(root, "sheet"):
        props = {p[1]: p[2] for p in kids(sh, "property")}
        su = kid(sh, "uuid")[1]
        sheet_paths[props["Sheetfile"]] = f"/{root_uuid}/{su}"
        if props["Sheetfile"] not in trees:
            rep.err("G2", f"root references missing sheet file {props['Sheetfile']}")
    pin_net = {}
    for f, t in trees.items():
        if f == root_name:
            continue
        path = sheet_paths.get(f)
        if path is None:
            rep.err("G2", f"{f} is not referenced by the root sheet")
            continue
        libs = {s[1]: s for s in kid(t, "lib_symbols")[1:]}
        points = {}      # key -> list of (kind, name)
        pins_at = []     # (ref, pin, key)
        for s in kids(t, "symbol"):
            lib_id = kid(s, "lib_id")[1]
            if lib_id not in libs:
                rep.err("G2", f"{f}: lib_id {lib_id} not embedded")
                continue
            at = kid(s, "at")
            x0, y0, ang = float(at[1]), float(at[2]), float(at[3])
            props = {p[1]: p[2] for p in kids(s, "property")}
            ref = props.get("Reference", "?")
            inst = kid(s, "instances")
            ipath = kid(kid(inst, "project"), "path") if inst else None
            if not ipath or ipath[1] != path:
                rep.err("G2", f"{f}: {ref} instance path {ipath and ipath[1]} != {path}")
            lp = lib_pins(libs[lib_id])
            inst_pins = {p[1] for p in kids(s, "pin")}
            if inst_pins != set(lp):
                rep.err("G2", f"{f}: {ref} pin list differs from its lib symbol")
            is_power = kid(libs[lib_id], "power") is not None
            for num, (px, py, pname, ptype) in lp.items():
                rx, ry = rot(px, py, ang)
                k = key(x0 + rx, y0 - ry)
                if is_power:
                    if ptype == "power_in":
                        points.setdefault(k, []).append(("power", props["Value"]))
                    else:
                        points.setdefault(k, []).append(("flag", ref))
                else:
                    pins_at.append((ref, num, k))
        for gl in kids(t, "global_label"):
            at = kid(gl, "at")
            points.setdefault(key(float(at[1]), float(at[2])), []).append(("label", gl[1]))
        for nc in kids(t, "no_connect"):
            at = kid(nc, "at")
            points.setdefault(key(float(at[1]), float(at[2])), []).append(("nc", ""))
        if kids(t, "wire") or kids(t, "label") or kids(t, "junction"):
            rep.err("G3", f"{f}: unexpected wires/local labels (generator uses pin-end labels only)")
        seen_pin_keys = {}
        for ref, num, k in pins_at:
            if k in seen_pin_keys:
                rep.err("G3", f"{f}: pins {seen_pin_keys[k]} and {ref}.{num} touch each other")
            seen_pin_keys[k] = f"{ref}.{num}"
            marks = points.get(k, [])
            names = {n for kind, n in marks if kind in ("label", "power")}
            if any(kind == "nc" for kind, _ in marks):
                if names:
                    rep.err("G3", f"{f}: {ref}.{num} has both a no-connect flag and a net")
                pin_net[(ref, num)] = NC
            elif len(names) == 1:
                pin_net[(ref, num)] = names.pop()
            elif not names:
                rep.err("G3", f"{f}: {ref}.{num} is not connected to anything")
            else:
                rep.err("G3", f"{f}: {ref}.{num} touches several nets {sorted(names)}")
    n_ok = 0
    for p in d.parts:
        for x in p.pins:
            got = pin_net.get((p.ref, x.num))
            if got is None:
                rep.err("G3", f"{p.ref}.{x.num} missing from the KiCad schematic")
            elif got != x.net:
                rep.err("G3", f"{p.ref}.{x.num}: schematic net {got} != netlist.py {x.net}")
            else:
                n_ok += 1
    rep.info.append(f"G3 KiCad schematic connectivity: {n_ok} pins match netlist.py")


def check_svg(d, rep, sdir):
    for block in NL.BLOCKS:
        f = os.path.join(sdir, f"{block}.svg")
        if not os.path.exists(f):
            rep.warn("G4", f"{f} missing (run generate.py)")
            continue
        try:
            root = ET.parse(f).getroot()
        except ET.ParseError as e:
            rep.err("G4", f"{block}.svg: {e}")
            continue
        texts = " ".join((t.text or "") for t in root.iter("{http://www.w3.org/2000/svg}text"))
        for p in d.parts:
            if p.block == block and not re.search(rf"\b{re.escape(p.ref)}\b", texts):
                rep.err("G4", f"{block}.svg does not show {p.ref}")
    mech = os.path.join(sdir, "mechanical.svg")
    if not os.path.exists(mech):
        rep.warn("G4", "mechanical.svg missing (run generate.py)")
    else:
        try:
            ET.parse(mech)
        except ET.ParseError as e:
            rep.err("G4", f"mechanical.svg: {e}")


BOM_COLUMNS = ["reference", "qty", "value", "manufacturer", "mpn", "lcsc", "lcsc_confidence", "lcsc_evidence",
               "jlc_type", "assembly", "dnp", "unit_price_usd_est", "price_basis", "footprint", "confidence",
               "notes"]


def check_bom_json(d, rep):
    bom = os.path.join(HERE, "bom.csv")
    refs = {p.ref: p for p in d.parts}
    if os.path.exists(bom):
        seen = []
        with open(bom, encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            if reader.fieldnames != BOM_COLUMNS:
                rep.err("G5", f"bom.csv columns {reader.fieldnames} != {BOM_COLUMNS}")
                return
            for row in reader:
                rrefs = row["reference"].split()
                if int(row["qty"]) != len(rrefs):
                    rep.err("G5", f"bom.csv: qty {row['qty']} != {len(rrefs)} refs ({row['reference']})")
                seen += rrefs
                if not row["mpn"]:
                    rep.err("G5", f"bom.csv: line {row['reference']} has no MPN")
                code = row["lcsc"]
                if code and not LCSC_RE.fullmatch(code):
                    rep.err("G5", f"bom.csv: malformed LCSC code {code!r}")
                if code and row["lcsc_confidence"] not in (CONFIRMED, LIKELY):
                    rep.err("G5", f"bom.csv: LCSC code {code} without a confidence label")
                if not code and row["lcsc_confidence"]:
                    rep.err("G5", f"bom.csv: {row['reference']} has a confidence label but no LCSC code")
                pcb_only = row["assembly"].startswith("none")
                if not code and not pcb_only and "choose at order" not in row["notes"]:
                    rep.err("G5", f"bom.csv: {row['reference']} has no LCSC code and no 'choose at order' note")
                if not pcb_only and not row["unit_price_usd_est"]:
                    rep.err("G5", f"bom.csv: {row['reference']} has no unit price estimate")
                for r in rrefs:
                    p = refs.get(r)
                    if p is None or p.order is None:
                        continue
                    o = p.order
                    got = (row["manufacturer"], row["mpn"], row["lcsc"], row["dnp"] == "yes")
                    want = (o.manufacturer, o.mpn, o.lcsc, p.dnp)
                    if got != want:
                        rep.err("G5", f"bom.csv {r}: {got} != netlist.py {want}")
                    if o.price is not None and row["unit_price_usd_est"] != f"{o.price:.4f}":
                        rep.err("G5", f"bom.csv {r}: price {row['unit_price_usd_est']} != {o.price:.4f}")
        want = sorted(refs)
        if sorted(seen) != want:
            rep.err("G5", f"bom.csv refs differ from netlist.py ({len(seen)} vs {len(want)})")
    else:
        rep.warn("G5", "bom.csv missing")
    js = os.path.join(HERE, "netlist.json")
    if os.path.exists(js):
        data = json.load(open(js, encoding="utf-8"))
        net_pins = d.net_pins()
        jn = {n: sorted(map(tuple, v["nodes"])) for n, v in data["nets"].items()}
        dn = {n: sorted((p.ref, x.num) for p, x in v) for n, v in net_pins.items()}
        if jn != dn:
            rep.err("G5", "netlist.json nets differ from netlist.py (re-run generate.py)")
        jm = {c["ref"]: (c["mpn"], c["lcsc"]) for c in data["components"]}
        dm = {p.ref: (p.mpn, p.lcsc) for p in d.parts}
        if jm != dm:
            rep.err("G5", "netlist.json MPN/LCSC fields differ from netlist.py (re-run generate.py)")
    else:
        rep.warn("G5", "netlist.json missing")


def bom_summary(d):
    """Coverage of the ordering data, per unique orderable part (DNP included)."""
    orders = {}
    for p in d.parts:
        if p.order and p.order.assembly != NL.PCB:
            orders[(p.order.mpn, p.dnp, p.footprint, p.conf)] = p.order
    n = len(orders)
    conf = sum(1 for o in orders.values() if o.lcsc and o.lcsc_conf == CONFIRMED)
    likely = sum(1 for o in orders.values() if o.lcsc and o.lcsc_conf == LIKELY)
    empty = sum(1 for o in orders.values() if not o.lcsc)
    return [f"BOM: {n} orderable lines, {sum(1 for o in orders.values() if o.mpn)} with MPN; LCSC code "
            f"[Confirmed] {conf}, [Likely] {likely}, none {empty}"]


def stats(d, rep):
    unk = {}
    maybe = {}
    for p in d.parts:
        for x in p.pins:
            if x.conf == UNKNOWN:
                unk[p.ref] = unk.get(p.ref, 0) + 1
            elif x.conf == LIKELY:
                maybe[p.ref] = maybe.get(p.ref, 0) + 1
    for ref, n in sorted(unk.items()):
        rep.warn("W2", f"{ref}: {n} pin(s) [Unknown] - need the vendor datasheet before layout")
    for ref, n in sorted(maybe.items()):
        rep.warn("W3", f"{ref}: {n} pin(s) [Likely] - confirm against the full datasheet")
    parts_unknown = [p.ref for p in d.parts if p.conf == UNKNOWN]
    if parts_unknown:
        rep.warn("W4", f"values/choices [Unknown]: {', '.join(parts_unknown)}")
    no_code = sorted({p.order.mpn for p in d.parts if p.order and not p.order.lcsc
                      and p.order.assembly != NL.PCB})
    if no_code:
        rep.warn("W5", f"no LCSC code (choose at order): {', '.join(no_code)}")


def run(d, with_files=True):
    rep = Report()
    check_design(d, rep)
    if with_files:
        check_kicad(d, rep, os.path.join(HERE, "kicad"))
        check_svg(d, rep, os.path.join(HERE, "svg"))
        check_bom_json(d, rep)
    stats(d, rep)
    return rep


def selftest():
    """Inject one fault per rule into a copy of the design and make sure it is reported."""
    base = NL.build()
    cases = []

    def case(name, rule, mutate):
        d = copy.deepcopy(base)
        mutate(d)
        rep = run(d, with_files=False)
        hit = any(e.startswith(rule) for e in rep.errors)
        cases.append((name, rule, hit))

    def unassign(d):
        d.part("U301").pin("59").net = None
    case("pin neither on a net nor NC", "R2", unassign)

    def single(d):
        d.part("U301").pin("62").net = "ORPHAN_NET"
    case("single-pin net", "R3", single)

    def nodecap(d):
        d.part("U301").pin("19").decap = ()
    case("IC power pin without decoupling", "R4", nodecap)

    def wrongcap(d):
        d.part("U301").pin("19").decap = ("C301",)
    case("decap on the wrong net", "R4", wrongcap)

    def collide(d):
        d.part("U301").pin("62").net = "3v3"
        d.part("U301").pin("61").net = "3v3"
    case("net name collision 3v3 vs 3V3", "R5", collide)

    def pair(d):
        d.part("U205").pin("3").net = "PHONE_USB_DP"
        d.part("U205").pin("4").net = "PHONE_USB_DP"
    case("broken differential pair", "R6", pair)

    def dup(d):
        d.parts.append(copy.deepcopy(d.part("R101")))
    case("duplicate reference", "R1", dup)

    def fb(d):
        d.part("R110").value = "10k 1%"
    case("wrong regulator feedback", "R10", fb)

    def placeholder(d):
        d.part("U201").pin("?XTALI").conf = LIKELY
    case("placeholder pin not marked unknown", "R9", placeholder)

    def nobuy(d):
        d.part("C301").order = None
    case("part without ordering data", "R11", nobuy)

    def badcode(d):
        d.part("U301").order = NL.Buy("WCH", "CH32V305RBT6", "5187529", "WEB")
        d.part("U301").lcsc = "5187529"
    case("malformed LCSC code", "R11", badcode)

    def fakeclass(d):
        d.part("U101").order = NL.Buy("WCH", "CH224K", "C970725", "WEB", "Basic", 0.33)
    case("JLCPCB class without evidence", "R11", fakeclass)

    ok = True
    for name, rule, hit in cases:
        print(f"  selftest {rule:<4} {name:<38} {'caught' if hit else 'NOT CAUGHT'}")
        ok &= hit
    # the untouched design must be clean
    clean = not run(copy.deepcopy(base), with_files=False).errors
    print(f"  selftest      untouched design passes              {'yes' if clean else 'NO'}")
    return ok and clean


def main():
    verbose = "-v" in sys.argv
    d = NL.build()
    rep = run(d)
    npins = sum(len(p.pins) for p in d.parts)
    nnc = sum(1 for p in d.parts for x in p.pins if x.net == NC)
    net_pins = d.net_pins()
    print(f"box-v1 check: {len(d.parts)} parts ({sum(p.dnp for p in d.parts)} DNP), {npins} pins "
          f"({nnc} no-connect), {len(net_pins)} nets")
    for line in rep.info:
        print("  " + line)
    conf = {CONFIRMED: 0, LIKELY: 0, UNKNOWN: 0}
    for p in d.parts:
        for x in p.pins:
            conf[x.conf] += 1
    print(f"  pins by confidence: [Confirmed] {conf[CONFIRMED]}, [Likely] {conf[LIKELY]}, "
          f"[Unknown] {conf[UNKNOWN]}")
    for line in bom_summary(d):
        print("  " + line)
    shown = rep.warnings if verbose else [w for w in rep.warnings if w.startswith(("W2", "G"))]
    print(f"  warnings: {len(rep.warnings)}" + ("" if verbose else " (use -v for all)"))
    for w in shown:
        print("    " + w)
    ok = True
    if "--selftest" in sys.argv:
        ok = selftest()
    if rep.errors:
        print(f"ERRORS: {len(rep.errors)}")
        for e in rep.errors:
            print("  " + e)
        ok = False
    print("RESULT: " + ("PASS" if ok else "FAIL"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
