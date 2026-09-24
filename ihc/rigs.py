"""Zero-config discovery of the rigs attached to this host (one rig = HID chip + capture card).

Plug-and-play is what a hardware box needs: at start-up every USB serial port is probed for a
device speaking the CH9329 protocol (CH9329 cable or ESP32 bridge; the baud rate is found
automatically), every V4L2 capture node is listed, and the two are paired by the USB hub they hang
off (sysfs topology). A lone chip and a lone capture card are paired with each other. Rig ids come
from the chip's physical USB port, so a phone keeps its id and calibration across reboots as long as
its cables stay in the same ports.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .hid.scan import SCAN_BAUDS, candidate_ports, scan_port

_USB_PORT_DIR = re.compile(r"^\d+-[\d.]+$")  # sysfs name of a USB device: bus-port.port...


@dataclass
class ChipFound:
    port: str
    baud: int
    addr: int
    info: dict
    usb_port: str | None  # e.g. "1-1.2"


@dataclass
class VideoFound:
    device: str
    name: str
    usb_port: str | None


@dataclass
class RigSpec:
    id: str
    chip: ChipFound
    video: VideoFound | None
    notes: list[str] = field(default_factory=list)


def usb_port_of(node: str, sysfs: str = "/sys") -> str | None:
    """USB port path ("1-1.2") of the device behind /dev/ttyUSB0 or /dev/video0, from sysfs."""
    name = os.path.basename(os.path.realpath(node))
    cls = "video4linux" if name.startswith("video") else "tty"
    try:
        path = Path(os.path.realpath(Path(sysfs) / "class" / cls / name / "device"))
    except OSError:
        return None
    for part in reversed(path.parts):
        if _USB_PORT_DIR.match(part):
            return part
    return None


def hub_of(usb_port: str | None) -> str | None:
    """"1-1.2" -> "1-1" (the hub); a device straight on a root port has no hub of its own."""
    if not usb_port or "." not in usb_port:
        return None
    return usb_port.rsplit(".", 1)[0]


def find_chips(ports: list[str] | None = None, *, sysfs: str = "/sys", timeout: float = 0.25,
               bauds=SCAN_BAUDS, log=None, skip: set[str] = frozenset()) -> list[ChipFound]:
    """Serial ports where a CH9329-protocol device answers GET_INFO (ports whose real path is in
    `skip`, i.e. already in use, are left alone)."""
    if ports is None:  # stable by-path names survive re-enumeration after an unplug
        ports = [(pi.by_path or pi.by_id or [pi.device])[0] for pi in candidate_ports()]
    found = []
    for port in ports:
        if os.path.realpath(port) in skip:
            continue
        try:
            results = scan_port(port, bauds, timeout=timeout)
        except Exception as e:  # busy, vanished, permission: report and keep going
            if log:
                log("rig_probe_error", port=port, error=str(e))
            continue
        ok = [r for r in results if r.ok]
        if ok:
            r = ok[0]
            found.append(ChipFound(port, r.baud, r.addr, r.info, usb_port_of(port, sysfs)))
        if log:
            log("rig_probe", port=port, found=bool(ok), baud=ok[0].baud if ok else None)
    return found


def find_videos(sysfs: str = "/sys") -> list[VideoFound]:
    """V4L2 capture nodes (index 0 of each device; UVC metadata nodes are skipped)."""
    out = []
    base = Path(sysfs) / "class" / "video4linux"
    for node in sorted(base.glob("video*")) if base.is_dir() else []:
        try:
            if (node / "index").read_text().strip() != "0":
                continue
            name = (node / "name").read_text().strip()
        except OSError:
            continue
        dev = f"/dev/{node.name}"
        out.append(VideoFound(dev, name, usb_port_of(dev, sysfs)))
    return out


def pair(chips: list[ChipFound], videos: list[VideoFound]) -> list[RigSpec]:
    rigs, used = [], set()
    by_hub = {}
    for v in videos:
        hub = hub_of(v.usb_port)
        if hub:
            by_hub.setdefault(hub, []).append(v)
    for c in chips:
        rig_id = "rig-" + (c.usb_port or os.path.basename(c.port))
        cands = [v for v in by_hub.get(hub_of(c.usb_port), []) if v.device not in used]
        video, notes = None, []
        if len(cands) == 1:
            video = cands[0]
        elif len(cands) > 1:
            notes.append(f"several capture cards on hub {hub_of(c.usb_port)}: pair it in a config file")
        rigs.append(RigSpec(rig_id, c, video, notes))
        if video:
            used.add(video.device)
    unpaired_rigs = [r for r in rigs if r.video is None and not r.notes]
    free_videos = [v for v in videos if v.device not in used]
    if len(unpaired_rigs) == 1 and len(free_videos) == 1:  # one chip, one card: they belong together
        unpaired_rigs[0].video = free_videos[0]
        unpaired_rigs[0].notes.append("paired as the only chip and the only capture card")
    for r in rigs:
        if r.video is None and not r.notes:
            r.notes.append("no capture card found for this chip (HID only)")
    return rigs


def find_rigs(ports: list[str] | None = None, *, sysfs: str = "/sys", timeout: float = 0.25,
              log=None, videos: Callable[[], list[VideoFound]] | None = None,
              skip: set[str] = frozenset(), skip_videos: set[str] = frozenset()) -> list[RigSpec]:
    """Rigs made of chips and capture cards not in use yet (`skip`, `skip_videos`: real paths)."""
    chips = find_chips(ports, sysfs=sysfs, timeout=timeout, log=log, skip=skip)
    free = [v for v in (videos() if videos else find_videos(sysfs)) if os.path.realpath(v.device) not in skip_videos]
    return pair(chips, free)
