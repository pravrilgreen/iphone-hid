#!/usr/bin/env python3
"""hid_loopback: check what the HID chip really delivers, with its HID end plugged into this Linux host.

The chip's USB side shows up here as input devices; this tool sends reports over the serial side and
reads back what Linux received (evdev), so lost, merged or late reports become visible before any
iPhone is involved. The devices are grabbed (EVIOCGRAB) during the test, so the host's own desktop
does not move or type.

    python tools/hid_loopback.py --port /dev/ttyUSB0 list           # the chip's input devices
    python tools/hid_loopback.py --port /dev/ttyUSB0 rel            # relative reports at several paces
    python tools/hid_loopback.py --port /dev/ttyUSB0 abs            # absolute reports (grid)
    python tools/hid_loopback.py --port /dev/ttyUSB0 keys           # typing
    python tools/hid_loopback.py --port /dev/ttyUSB0 all

Reading /dev/input/event* needs root or the `input` group.
"""

from __future__ import annotations

import argparse
import fcntl
import os
import select
import statistics
import struct
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ihc.hid.ch9329 import CH9329Backend  # noqa: E402
from ihc.input import keymap  # noqa: E402
from ihc.jsonlog import EventLog  # noqa: E402

EV_SYN, EV_KEY, EV_REL, EV_ABS = 0x00, 0x01, 0x02, 0x03
REL_X, REL_Y, REL_WHEEL = 0x00, 0x01, 0x08
ABS_X, ABS_Y = 0x00, 0x01
EVENT = struct.Struct("llHHi")  # struct input_event: timeval (2 x long), type, code, value
EVIOCGRAB = 0x40044590
EVIOCSCLOCKID = 0x400445A0
CLOCK_MONOTONIC = 1
LOG_DIR = ROOT / "docs" / "test-logs"


@dataclass
class InputNode:
    path: str
    name: str
    vendor: str
    product: str
    caps: dict = field(default_factory=dict)


def find_nodes(vendor: str | None = "1a86", product: str | None = None) -> list[InputNode]:
    """Input event nodes of a USB device (default: any WCH device, e.g. the CH9329 at 1a86:e129)."""
    out = []
    for ev in sorted(Path("/sys/class/input").glob("event*")):
        dev = ev / "device"
        try:
            v = (dev / "id" / "vendor").read_text().strip()
            p = (dev / "id" / "product").read_text().strip()
            name = (dev / "name").read_text().strip()
        except OSError:
            continue
        if vendor and v.lower() != vendor.lower():
            continue
        if product and p.lower() != product.lower():
            continue
        caps = {}
        for kind in ("rel", "abs", "key"):
            try:
                caps[kind] = (dev / "capabilities" / kind).read_text().strip()
            except OSError:
                pass
        out.append(InputNode(f"/dev/input/{ev.name}", name, v, p, caps))
    return out


class EventReader:
    """Grabs the nodes and collects events with CLOCK_MONOTONIC timestamps."""

    def __init__(self, nodes: list[InputNode]):
        self.fds = []
        for n in nodes:
            fd = os.open(n.path, os.O_RDONLY | os.O_NONBLOCK)
            fcntl.ioctl(fd, EVIOCSCLOCKID, struct.pack("i", CLOCK_MONOTONIC))
            fcntl.ioctl(fd, EVIOCGRAB, 1)
            self.fds.append(fd)

    def drain(self, wait: float = 0.3) -> list[tuple[float, int, int, int]]:
        """(time, type, code, value) events until `wait` seconds pass with nothing new."""
        events = []
        deadline = time.monotonic() + wait
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                return events
            ready, _, _ = select.select(self.fds, [], [], left)
            for fd in ready:
                data = os.read(fd, EVENT.size * 256)
                for i in range(0, len(data) - EVENT.size + 1, EVENT.size):
                    sec, usec, typ, code, value = EVENT.unpack_from(data, i)
                    events.append((sec + usec / 1e6, typ, code, value))
                deadline = time.monotonic() + wait

    def close(self) -> None:
        for fd in self.fds:
            try:
                fcntl.ioctl(fd, EVIOCGRAB, 0)
            finally:
                os.close(fd)


def summarize_rel(events, sent_at: list[float], dx: int) -> dict:
    """Compare what was sent (len(sent_at) reports of dx) with what arrived."""
    xs = [(t, v) for t, typ, code, v in events if typ == EV_REL and code == REL_X]
    total = sum(v for _, v in xs)
    expected = dx * len(sent_at)
    lat = [t - s for (t, _), s in zip(xs, sent_at)] if len(xs) == len(sent_at) else []
    return {
        "sent": len(sent_at),
        "received_events": len(xs),
        "total_expected": expected,
        "total_received": total,
        "lost_units": expected - total,
        "merged_reports": len(sent_at) - len(xs) if total == expected else None,
        "latency_ms_p50": round(statistics.median(lat) * 1000, 2) if lat else None,
        "latency_ms_max": round(max(lat) * 1000, 2) if lat else None,
    }


def test_rel(hid: CH9329Backend, reader: EventReader, intervals: list[float], count: int, log) -> list[dict]:
    results = []
    for mode in ("ack", "pipelined"):
        for interval in intervals:
            reader.drain(0.2)
            hid.wait_ack = mode == "ack"
            sent_at, dx = [], 1
            next_at = time.monotonic()
            for i in range(count):
                now = time.monotonic()
                if now < next_at:
                    time.sleep(next_at - now)
                sent_at.append(time.monotonic())
                hid.mouse_rel(dx, 0)
                next_at = sent_at[-1] + interval
            hid.wait_ack = True
            hid.sync()
            back = reader.drain(0.5)
            left = count * dx  # move back where it was (not measured)
            while left > 0:
                hid.mouse_rel(-min(100, left), 0)
                left -= 100
            reader.drain(0.2)
            r = {"mode": mode, "interval_ms": interval * 1000, **summarize_rel(back, sent_at, dx),
                 "lost_acks": hid.stats["lost_acks"], "async_errors": len(hid.async_errors)}
            results.append(r)
            log("loopback_rel", **r)
            ok = "OK " if r["lost_units"] == 0 else "LOSS"
            print(f"{ok} {mode:9} every {interval * 1000:5.1f} ms: sent {r['sent']}, received {r['received_events']} events, "
                  f"units {r['total_received']}/{r['total_expected']}, latency p50 {r['latency_ms_p50']} ms max {r['latency_ms_max']} ms")
    return results


def test_abs(hid: CH9329Backend, reader: EventReader, log) -> list[dict]:
    results = []
    reader.drain(0.2)
    for gx in (0, 1024, 2048, 3072, 4095):
        for gy in (0, 2048, 4095):
            hid.mouse_abs(gx, gy)
            ev = reader.drain(0.15)
            ax = [v for _, typ, code, v in ev if typ == EV_ABS and code == ABS_X]
            ay = [v for _, typ, code, v in ev if typ == EV_ABS and code == ABS_Y]
            r = {"sent": [gx, gy], "abs_x": ax[-1] if ax else None, "abs_y": ay[-1] if ay else None}
            results.append(r)
            log("loopback_abs", **r)
            print(f"abs ({gx:4}, {gy:4}) -> ABS_X {r['abs_x']} ABS_Y {r['abs_y']}")
    return results


def test_keys(hid: CH9329Backend, reader: EventReader, log, text: str = "Hello, iPhone 123!") -> dict:
    reader.drain(0.2)
    for mods, keys in keymap.text_reports(text):
        hid.keyboard(mods, keys)
    ev = reader.drain(0.4)
    presses = [code for _, typ, code, v in ev if typ == EV_KEY and v == 1]
    releases = [code for _, typ, code, v in ev if typ == EV_KEY and v == 0]
    shift_presses = presses.count(42) + presses.count(54)  # KEY_LEFTSHIFT / KEY_RIGHTSHIFT
    r = {"text": text, "chars": len(text), "key_presses": len(presses) - shift_presses, "key_releases": len(releases),
         "all_released": len(presses) == len(releases)}
    log("loopback_keys", **r)
    print(f"typed {len(text)} chars: {r['key_presses']} key presses (shift not counted), all released: {r['all_released']}")
    return r


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", required=True)
    ap.add_argument("--baud", type=int, default=9600)
    ap.add_argument("--vendor", default="1a86", help="USB vendor id of the chip's HID side (hex)")
    ap.add_argument("--product", help="USB product id (hex), e.g. e129")
    ap.add_argument("--count", type=int, default=300, help="reports per pace in the rel test")
    ap.add_argument("--intervals", default="25,16,10,5,2", help="paces to try, in ms")
    ap.add_argument("--log", help="JSON-lines log (default docs/test-logs/loopback-<time>.jsonl)")
    ap.add_argument("test", choices=["list", "rel", "abs", "keys", "all"])
    args = ap.parse_args(argv)

    nodes = find_nodes(args.vendor, args.product)
    if args.test == "list" or not nodes:
        for n in nodes:
            print(f"{n.path}  {n.vendor}:{n.product}  {n.name}  caps {n.caps}")
        if not nodes:
            print("no input devices from the chip: plug its HID end into this host (lsusb should show it)")
            return 1
        return 0
    log = EventLog(args.log or LOG_DIR / f"loopback-{time.strftime('%Y%m%d-%H%M%S')}.jsonl")
    log("loopback_start", port=args.port, baud=args.baud, nodes=[n.__dict__ for n in nodes])
    try:
        reader = EventReader(nodes)
    except PermissionError:
        print("permission denied on /dev/input/event*: run with sudo or add yourself to the 'input' group")
        return 2
    try:
        with CH9329Backend(args.port, args.baud, trace=None) as hid:
            print(f"chip: {hid.info()}")
            if args.test in ("rel", "all"):
                test_rel(hid, reader, [float(x) / 1000 for x in args.intervals.split(",")], args.count, log)
            if args.test in ("abs", "all"):
                test_abs(hid, reader, log)
            if args.test in ("keys", "all"):
                test_keys(hid, reader, log)
            hid.release_all()
    finally:
        reader.close()
        log.close()
    print(f"log: {log.path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
