"""Find the serial port, baud rate and address a CH9329 answers on. Read-only: never writes config.

    python -m ihc.hid.scan --list                   # serial ports, with /dev/serial/by-id names
    python -m ihc.hid.scan                          # probe every likely USB-serial port
    python -m ihc.hid.scan --port /dev/ttyUSB0      # probe one port at 9600..115200
    python -m ihc.hid.scan --port /dev/ttyUSB0 --scan-addr   # also try addresses 0x01-0xFE
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from serial.tools import list_ports

from ..jsonlog import EventLog
from .base import HidError, HidPortError, HidTimeout
from .ch9329 import CH9329Backend, Trace

# 9600 is the factory default and 115200 the usual upgrade, so try those first.
SCAN_BAUDS = (9600, 115200, 57600, 38400, 19200)

USB_SERIAL_CHIPS = {
    (0x1A86, 0x7523): "CH340",
    (0x1A86, 0x5523): "CH341",
    (0x1A86, 0x55D4): "CH9102",
    (0x1A86, 0x55D3): "CH343",
    (0x10C4, 0xEA60): "CP210x",
    (0x0403, 0x6001): "FT232R",
    (0x0403, 0x6015): "FT231X",
    (0x303A, 0x1001): "ESP32-S3 USB-CDC",
}


@dataclass
class PortInfo:
    device: str
    description: str
    vid: int | None
    pid: int | None
    serial_number: str | None
    by_id: list[str] = field(default_factory=list)
    by_path: list[str] = field(default_factory=list)

    @property
    def chip(self) -> str | None:
        return USB_SERIAL_CHIPS.get((self.vid, self.pid)) if self.vid is not None else None

    def to_dict(self) -> dict:
        return {
            "device": self.device,
            "description": self.description,
            "usb": f"{self.vid:04x}:{self.pid:04x}" if self.vid is not None else None,
            "chip": self.chip,
            "serial_number": self.serial_number,
            "by_id": self.by_id,
            "by_path": self.by_path,
        }


def _links(directory: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    d = Path(directory)
    if d.is_dir():
        for link in sorted(d.iterdir()):
            out.setdefault(os.path.realpath(link), []).append(str(link))
    return out


def list_serial_ports() -> list[PortInfo]:
    by_id, by_path = _links("/dev/serial/by-id"), _links("/dev/serial/by-path")
    ports = []
    for cp in sorted(list_ports.comports(), key=lambda c: c.device):
        real = os.path.realpath(cp.device)
        ports.append(
            PortInfo(cp.device, cp.description, cp.vid, cp.pid, cp.serial_number, by_id.get(real, []), by_path.get(real, []))
        )
    return ports


def candidate_ports(ports: Iterable[PortInfo] | None = None) -> list[PortInfo]:
    """USB serial ports (known bridge chips first); built-in UARTs like /dev/ttyS* are left out."""
    usb = [pi for pi in (ports if ports is not None else list_serial_ports()) if pi.vid is not None]
    return sorted(usb, key=lambda pi: pi.chip is None)


@dataclass
class ProbeResult:
    port: str
    baud: int
    addr: int
    info: dict | None = None
    garbage: bytes = b""
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.info is not None

    def summary(self) -> str:
        if self.ok:
            i = self.info
            usb = "connected" if i["usb_connected"] else "NOT connected"
            return f"OK  chip {i['version']}, USB side {usb} (addr {self.addr:#04x})"
        if self.garbage:
            return f"--  {len(self.garbage)} garbage byte(s): {self.garbage[:16].hex(' ')}"
        return f"--  {self.error or 'no reply'}"


def _probe(hid: CH9329Backend, result: ProbeResult, attempts: int) -> ProbeResult:
    for _ in range(attempts):
        try:
            result.info = hid.info()
            result.error = None
            return result
        except HidTimeout as e:
            result.garbage += e.received
            result.error = "no reply"
        except HidError as e:
            result.error = str(e)
    return result


def probe(port: str, baud: int, addr: int = 0, timeout: float = 0.25, attempts: int = 2, trace: Trace | None = None) -> ProbeResult:
    """Send GET_INFO at one baud/address. Port errors propagate; timeouts become a failed result."""
    with CH9329Backend(port, baud, addr=addr, timeout=timeout, trace=trace) as hid:
        return _probe(hid, ProbeResult(port, baud, addr), attempts)


def scan_port(
    port: str,
    bauds: Iterable[int] = SCAN_BAUDS,
    *,
    scan_addr: bool = False,
    stop_at_first: bool = True,
    timeout: float = 0.25,
    trace: Trace | None = None,
    progress: Callable[[ProbeResult], None] | None = None,
) -> list[ProbeResult]:
    """Probe each baud at address 0 (a chip left at address 0 answers any address); with
    `scan_addr`, also sweep 0x01-0xFE at bauds where address 0 got no answer."""
    results = []
    for baud in bauds:
        with CH9329Backend(port, baud, timeout=timeout, trace=trace) as hid:
            r = _probe(hid, ProbeResult(port, baud, 0), attempts=2)
            if not r.ok and scan_addr:
                hid.timeout = 0.05
                for addr in range(0x01, 0xFF):
                    hid.addr = addr
                    hit = _probe(hid, ProbeResult(port, baud, addr), attempts=1)
                    if hit.ok:
                        r = hit
                        break
        results.append(r)
        if progress:
            progress(r)
        if r.ok and stop_at_first:
            break
    return results


def _print_ports(ports: list[PortInfo]) -> None:
    if not ports:
        print("no serial ports found. Is the USB-serial end of the cable plugged into this host?")
        return
    for pi in ports:
        usb = f"{pi.vid:04x}:{pi.pid:04x}" if pi.vid is not None else "----:----"
        print(f"{pi.device:14} {usb}  {pi.chip or '':16} {pi.description}")
        for link in pi.by_id + pi.by_path:
            print(f"{'':14} -> {link}")


def advice(results: list[ProbeResult]) -> list[str]:
    found = [r for r in results if r.ok]
    if found:
        r = found[0]
        lines = [f"use: --port {r.port} --baud {r.baud}" + (f" --addr {r.addr:#04x}" if r.addr else "")]
        if not r.info["usb_connected"]:
            lines.append(
                "the chip answers but its USB side is not enumerated: plug the HID end into the "
                "iPhone/hub (or a PC to test), unlock the iPhone and allow the accessory"
            )
        return lines
    if any(r.garbage for r in results):
        return [
            "bytes came back but never a valid frame: the chip may use a baud rate outside this "
            "list, or it is not in protocol mode (pull its SET pin low to force protocol mode)"
        ]
    return [
        "no reply at any baud rate. Check that the HID end is plugged in (the CH9329 is usually "
        "powered from that side), that this is the right port, and TX/RX wiring on bare modules. "
        "If the address was changed, retry with --scan-addr. Last resort: factory reset by "
        "holding the chip's DEF pin low for 3 s."
    ]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m ihc.hid.scan", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="only list serial ports")
    ap.add_argument("--port", action="append", help="port to probe (repeatable); default: every USB serial port")
    ap.add_argument("--bauds", default=",".join(map(str, SCAN_BAUDS)), help="comma-separated baud rates to try")
    ap.add_argument("--scan-addr", action="store_true", help="also try chip addresses 0x01-0xFE (slow)")
    ap.add_argument("--all", action="store_true", help="keep probing after the first hit")
    ap.add_argument("--timeout", type=float, default=0.25, help="seconds to wait for each reply")
    ap.add_argument("--log", help="write a JSON-lines trace here")
    args = ap.parse_args(argv)

    ports = list_serial_ports()
    if args.list:
        _print_ports(ports)
        return 0

    log = None
    if args.log:
        log = EventLog(args.log)
        log("scan_start", argv=sys.argv, ports=[pi.to_dict() for pi in ports])
    targets = args.port or [pi.device for pi in candidate_ports(ports)]
    if not targets:
        _print_ports(ports)
        print("no USB serial port to probe; pass --port explicitly")
        return 2

    bauds = [int(b) for b in args.bauds.split(",") if b.strip()]
    all_results = []
    for port in targets:
        print(f"probing {port} ...")
        try:
            results = scan_port(
                port,
                bauds,
                scan_addr=args.scan_addr,
                stop_at_first=not args.all,
                timeout=args.timeout,
                trace=log,
                progress=lambda r: print(f"  {r.baud:>6} baud: {r.summary()}"),
            )
        except HidPortError as e:
            print(f"  {e}")
            if log:
                log("scan_port_error", port=port, error=str(e))
            continue
        all_results += results
        if log:
            for r in results:
                log("probe", port=r.port, baud=r.baud, addr=r.addr, ok=r.ok, info=r.info, garbage=r.garbage.hex(" "), error=r.error)
    for line in advice(all_results):
        print(line)
    if log:
        log("scan_done", found=[{"port": r.port, "baud": r.baud, "addr": r.addr} for r in all_results if r.ok])
        log.close()
    return 0 if any(r.ok for r in all_results) else 1


if __name__ == "__main__":
    sys.exit(main())
