"""Linux USB gadget backend: the box itself is the phone's keyboard and mouse (no CH9329, no serial).

For boards whose USB controller can work as a device: the Orange Pi 5 Plus Type-C port, the USB-C
port of a Raspberry Pi 4/5, the data port of a Pi Zero 2 W, and so on. The kernel's HID function
(f_hid) is set up through configfs (`gadget_up`, or `sudo ihc gadget up`): one USB interface per
report type, without report IDs, in the order keyboard, consumer control, system control, relative
mouse, absolute pointer (the order the ESP32 bridge uses). Each interface is a /dev/hidgN node.

Delivery: f_hid keeps one report in flight per interface. write() queues it, and poll() reports the
node writable again only once the host has taken the report (its IN transfer completed). So a
report counts as delivered when the phone has actually polled it, which is a stronger ack than the
CH9329's (the chip received the frame). A phone that stops polling (locked, unplugged, accessory not
allowed) shows up as a timeout, and a write while the phone has not enumerated the gadget fails at
once.

The phone's keyboard LEDs (Caps Lock...) come back as output reports on the keyboard node.
/sys/class/udc/<udc>/state reads "configured" once the phone has enumerated the gadget.
"""

from __future__ import annotations

import errno
import os
import select
import threading
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from . import protocol as p
from .base import HidError, HidPortError, HidStatusError, HidTimeout

CONFIGFS = "/sys/kernel/config/usb_gadget"
DEFAULT_NAME = "ihc"
LINUX_FOUNDATION_VID = 0x1D6B
COMPOSITE_GADGET_PID = 0x0104

# -- report descriptors (HID 1.11 and the HID Usage Tables; the same collections as the ESP32
#    bridge firmware, each on its own interface, so without report IDs) --------------------------

DESC_KEYBOARD = bytes([
    0x05, 0x01, 0x09, 0x06, 0xA1, 0x01,  # Generic Desktop / Keyboard / Collection (Application)
    0x05, 0x07, 0x19, 0xE0, 0x29, 0xE7,  # Keyboard page, Left Control .. Right GUI
    0x15, 0x00, 0x25, 0x01, 0x75, 0x01, 0x95, 0x08, 0x81, 0x02,  # 8 modifier bits
    0x95, 0x01, 0x75, 0x08, 0x81, 0x01,  # reserved byte
    0x05, 0x08, 0x19, 0x01, 0x29, 0x05, 0x95, 0x05, 0x75, 0x01, 0x91, 0x02,  # 5 LED bits (output)
    0x95, 0x01, 0x75, 0x03, 0x91, 0x01,  # LED padding
    0x05, 0x07, 0x19, 0x00, 0x2A, 0xFF, 0x00, 0x15, 0x00, 0x26, 0xFF, 0x00,
    0x95, 0x06, 0x75, 0x08, 0x81, 0x00,  # 6 key usages (array)
    0xC0,
])

# 24 one-bit consumer usages in CH9329 multimedia bit order (protocol.MEDIA_KEYS).
CONSUMER_USAGES = (0x0E9, 0x0EA, 0x0E2, 0x0CD, 0x0B5, 0x0B6, 0x0B7, 0x0B8, 0x18A, 0x221, 0x22A, 0x223,
                   0x224, 0x225, 0x226, 0x227, 0x183, 0x196, 0x192, 0x1B1, 0x194, 0x206, 0x0B2, 0x0B4)
DESC_CONSUMER = bytes([
    0x05, 0x0C, 0x09, 0x01, 0xA1, 0x01,  # Consumer / Consumer Control / Collection (Application)
    0x15, 0x00, 0x25, 0x01, 0x75, 0x01, 0x95, 0x18,
    *[b for u in CONSUMER_USAGES for b in (0x0A, u & 0xFF, u >> 8)],
    0x81, 0x02,
    0xC0,
])

DESC_SYSTEM = bytes([
    0x05, 0x01, 0x09, 0x80, 0xA1, 0x01,  # Generic Desktop / System Control
    0x15, 0x00, 0x25, 0x01, 0x75, 0x01, 0x95, 0x03,
    0x09, 0x81, 0x09, 0x82, 0x09, 0x83, 0x81, 0x02,  # power down, sleep, wake up
    0x95, 0x01, 0x75, 0x05, 0x81, 0x03,  # padding
    0xC0,
])

DESC_MOUSE = bytes([
    0x05, 0x01, 0x09, 0x02, 0xA1, 0x01,  # Generic Desktop / Mouse
    0x09, 0x01, 0xA1, 0x00,  # Pointer / Collection (Physical)
    0x05, 0x09, 0x19, 0x01, 0x29, 0x03, 0x15, 0x00, 0x25, 0x01, 0x95, 0x03, 0x75, 0x01, 0x81, 0x02,  # 3 buttons
    0x95, 0x01, 0x75, 0x05, 0x81, 0x03,  # padding
    0x05, 0x01, 0x09, 0x30, 0x09, 0x31, 0x09, 0x38,  # X, Y, wheel
    0x15, 0x81, 0x25, 0x7F, 0x75, 0x08, 0x95, 0x03, 0x81, 0x06,  # relative, -127..127
    0xC0, 0xC0,
])

DESC_ABS_POINTER = bytes([
    0x05, 0x01, 0x09, 0x02, 0xA1, 0x01,  # Generic Desktop / Mouse
    0x09, 0x01, 0xA1, 0x00,  # Pointer / Collection (Physical)
    0x05, 0x09, 0x19, 0x01, 0x29, 0x03, 0x15, 0x00, 0x25, 0x01, 0x95, 0x03, 0x75, 0x01, 0x81, 0x02,  # 3 buttons
    0x95, 0x01, 0x75, 0x05, 0x81, 0x03,  # padding
    0x05, 0x01, 0x09, 0x30, 0x09, 0x31,  # X, Y
    0x16, 0x00, 0x00, 0x26, 0xFF, 0x7F, 0x75, 0x10, 0x95, 0x02, 0x81, 0x02,  # absolute 0..32767
    0x09, 0x38, 0x15, 0x81, 0x25, 0x7F, 0x75, 0x08, 0x95, 0x01, 0x81, 0x06,  # relative wheel
    0xC0, 0xC0,
])

ABS_MAX = 32767
ABS_CENTRE = 16384


@dataclass(frozen=True)
class HidFunction:
    name: str
    protocol: int  # interface protocol: 1 keyboard, 2 mouse, 0 none
    subclass: int  # 1 = boot interface
    report_length: int
    descriptor: bytes
    out_reports: bool = False  # keyboard LEDs come back from the host


FUNCTIONS = {
    "keyboard": HidFunction("keyboard", 1, 1, 8, DESC_KEYBOARD, out_reports=True),
    "consumer": HidFunction("consumer", 0, 0, 3, DESC_CONSUMER),
    "system": HidFunction("system", 0, 0, 1, DESC_SYSTEM),
    "mouse": HidFunction("mouse", 2, 1, 4, DESC_MOUSE),
    "absolute": HidFunction("absolute", 0, 0, 6, DESC_ABS_POINTER),
}
ORDER = ("keyboard", "consumer", "system", "mouse", "absolute")

# Profiles, tagged like the bridge firmware's USB identities. iOS may keep the descriptor it saw for
# a known device, so each profile also gets its own serial number.
PROFILES = {
    "RA": ORDER,  # keyboard + extras + relative mouse + absolute pointer
    "A": ("keyboard", "consumer", "system", "absolute"),
    "R": ("keyboard", "consumer", "system", "mouse"),
    "K": ("keyboard",),
}


class GadgetError(HidError):
    """The gadget could not be set up, found or torn down."""


# -- configfs -------------------------------------------------------------------------------------


def list_udcs(sysfs: str = "/sys") -> list[str]:
    """USB device controllers of this board (empty: no port can act as a USB device right now)."""
    d = Path(sysfs) / "class" / "udc"
    return sorted(e.name for e in d.iterdir()) if d.is_dir() else []


def udc_state(udc: str, sysfs: str = "/sys") -> str | None:
    """'configured' once the host enumerated the gadget; 'not attached', 'suspended', ...; None if unknown."""
    if not udc:
        return None
    try:
        return (Path(sysfs) / "class" / "udc" / udc / "state").read_text().strip()
    except OSError:
        return None


def bound_udc(name: str = DEFAULT_NAME, configfs: str = CONFIGFS) -> str:
    try:
        return (Path(configfs) / name / "UDC").read_text().strip()
    except OSError:
        return ""


def udc_users(configfs: str = CONFIGFS) -> dict[str, str]:
    """UDC -> the configfs gadget bound to it (e.g. an ADB gadget from the board's own image)."""
    root = Path(configfs)
    out = {}
    for g in sorted(root.iterdir()) if root.is_dir() else []:
        udc = bound_udc(g.name, configfs)
        if udc:
            out[udc] = g.name
    return out


def gadget_functions(name: str = DEFAULT_NAME, configfs: str = CONFIGFS) -> list[str]:
    """Our HID functions linked into the gadget's configuration, in interface order."""
    conf = Path(configfs) / name / "configs" / "c.1"
    present = {e.name[4:] for e in conf.iterdir() if e.name.startswith("hid.")} if conf.is_dir() else set()
    return [f for f in ORDER if f in present]


def gadget_nodes(name: str = DEFAULT_NAME, configfs: str = CONFIGFS, sysfs: str = "/sys", dev: str = "/dev") -> dict[str, str]:
    """function -> /dev/hidgN, from each function's major:minor."""
    out = {}
    for fn in gadget_functions(name, configfs):
        try:
            majmin = (Path(configfs) / name / "functions" / f"hid.{fn}" / "dev").read_text().strip()
            uevent = (Path(sysfs) / "dev" / "char" / majmin / "uevent").read_text()
        except OSError:
            continue
        devname = next((line.split("=", 1)[1].strip() for line in uevent.splitlines() if line.startswith("DEVNAME=")), None)
        if devname:
            out[fn] = str(Path(dev) / devname)
    return out


def _write(path: Path, value: str | bytes) -> None:
    if isinstance(value, str):
        value = value.encode()
    with open(path, "wb", buffering=0) as f:  # configfs wants the whole value in one write
        f.write(value)


def _rmdir(path: Path) -> None:
    os.rmdir(path)


def gadget_up(name: str = DEFAULT_NAME, profile: str = "RA", *, udc: str | None = None, configfs: str = CONFIGFS,
              sysfs: str = "/sys", vendor: int = LINUX_FOUNDATION_VID, product: int = COMPOSITE_GADGET_PID,
              serial: str | None = None) -> str:
    """Create the HID gadget and bind it to a UDC (needs root). Returns the UDC name."""
    if profile not in PROFILES:
        raise GadgetError(f"unknown profile {profile!r}; one of {', '.join(PROFILES)}")
    root = Path(configfs)
    if not root.is_dir():
        raise GadgetError(f"{configfs} not found: load the gadget framework first (`sudo modprobe libcomposite`)")
    g = root / name
    if g.exists():
        raise GadgetError(f"a gadget named {name!r} already exists: tear it down first (`ihc gadget down`)")
    udcs = list_udcs(sysfs)
    if udc is None:
        if not udcs:
            raise GadgetError("no USB device controller on this board: the USB-C port is in host mode "
                              "(enable its peripheral/OTG mode in the device tree, see docs/gadget.md)")
        if len(udcs) > 1:
            raise GadgetError(f"several USB device controllers ({', '.join(udcs)}): choose one with --udc")
        udc = udcs[0]
    elif udcs and udc not in udcs:
        raise GadgetError(f"no USB device controller {udc!r}; this board has: {', '.join(udcs) or 'none'}")
    user = udc_users(configfs).get(udc)
    if user:
        raise GadgetError(f"{udc} is already used by the gadget {user!r} (often ADB): stop it first, e.g. "
                          f"`echo '' | sudo tee {configfs}/{user}/UDC`")
    try:
        g.mkdir()
        _write(g / "idVendor", f"{vendor:#06x}")
        _write(g / "idProduct", f"{product:#06x}")
        _write(g / "bcdDevice", "0x0100")
        _write(g / "bcdUSB", "0x0200")
        if (g / "max_speed").exists():
            _write(g / "max_speed", "high-speed")  # the phone side is USB 2 anyway (hub USB-A port)
        strings = g / "strings" / "0x409"
        strings.mkdir(parents=True, exist_ok=True)
        _write(strings / "manufacturer", "iphone-hid")
        _write(strings / "product", "ihc keyboard + mouse")
        _write(strings / "serialnumber", serial or f"ihc-{profile}")
        conf = g / "configs" / "c.1"
        (conf / "strings" / "0x409").mkdir(parents=True, exist_ok=True)
        _write(conf / "strings" / "0x409" / "configuration", "keyboard + mouse")
        _write(conf / "bmAttributes", "0x80")
        _write(conf / "MaxPower", "100")
        for fn in PROFILES[profile]:
            spec = FUNCTIONS[fn]
            f = g / "functions" / f"hid.{fn}"
            f.mkdir(parents=True, exist_ok=True)
            _write(f / "protocol", str(spec.protocol))
            _write(f / "subclass", str(spec.subclass))
            _write(f / "report_length", str(spec.report_length))
            _write(f / "report_desc", spec.descriptor)
            if not spec.out_reports and (f / "no_out_endpoint").exists():
                _write(f / "no_out_endpoint", "1")
            os.symlink(f, conf / f"hid.{fn}")
        _write(g / "UDC", udc)
    except OSError as e:
        try:
            gadget_down(name, configfs=configfs)
        except (OSError, GadgetError):
            pass
        raise GadgetError(f"cannot set up the gadget: {e}") from e
    return udc


def gadget_down(name: str = DEFAULT_NAME, *, configfs: str = CONFIGFS) -> bool:
    """Unbind and remove the gadget (needs root). False if there was none."""
    g = Path(configfs) / name
    if not g.exists():
        return False
    if bound_udc(name, configfs):
        _write(g / "UDC", "\n")
    conf = g / "configs" / "c.1"
    if conf.is_dir():
        for link in conf.iterdir():
            if link.is_symlink():
                link.unlink()
        if (conf / "strings" / "0x409").is_dir():
            _rmdir(conf / "strings" / "0x409")
        _rmdir(conf)
    functions = g / "functions"
    for f in sorted(functions.iterdir()) if functions.is_dir() else []:
        _rmdir(f)
    if (g / "strings" / "0x409").is_dir():
        _rmdir(g / "strings" / "0x409")
    _rmdir(g)
    return True


def gadget_status(name: str = DEFAULT_NAME, configfs: str = CONFIGFS, sysfs: str = "/sys", dev: str = "/dev") -> dict:
    udc = bound_udc(name, configfs)
    return {
        "name": name,
        "exists": (Path(configfs) / name).is_dir(),
        "udc": udc or None,
        "state": udc_state(udc, sysfs),
        "functions": gadget_functions(name, configfs),
        "nodes": gadget_nodes(name, configfs, sysfs, dev),
        "udcs": list_udcs(sysfs),
        "udc_users": udc_users(configfs),
    }


# -- the backend ----------------------------------------------------------------------------------


class HidgNode:
    """One /dev/hidgN. `pending`: a report was queued and the host has not been seen taking it."""

    def __init__(self, path: str, *, read: bool = False):
        self.path = path
        self.fd = os.open(path, (os.O_RDWR if read else os.O_WRONLY) | os.O_NONBLOCK)
        self._out = select.poll()
        self._out.register(self.fd, select.POLLOUT)
        self.pending = False
        self.lost = False  # the pending report was already counted as lost

    def write(self, report: bytes) -> None:
        n = os.write(self.fd, report)
        if n != len(report):
            raise OSError(errno.EIO, f"short write ({n} of {len(report)} bytes)")

    def writable(self, timeout: float) -> bool:
        """Wait until the report in flight was taken (the node accepts a new one)."""
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            for _, ev in self._out.poll(max(0.0, left) * 1000):
                if ev & select.POLLOUT:
                    return True
                if ev & (select.POLLERR | select.POLLHUP | select.POLLNVAL):
                    raise OSError(errno.EPIPE, "the gadget node reported an error")
            if left <= 0:
                return False

    def read_all(self) -> list[bytes]:
        out = []
        while True:
            try:
                data = os.read(self.fd, 64)
            except BlockingIOError:
                return out
            if not data:
                return out
            out.append(data)

    def close(self) -> None:
        try:
            os.close(self.fd)
        except OSError:
            pass


# write() errors meaning "the phone has not enumerated the gadget (or just dropped it)"
_NOT_CONNECTED = frozenset({errno.ESHUTDOWN, errno.ENODEV, errno.EPIPE, errno.ECONNRESET})


class GadgetBackend:
    """HidBackend over the kernel's HID gadget. Speaks the same API as CH9329Backend (reports on the
    CH9329's 4096 x 4096 absolute grid, MEDIA_KEYS and ACPI_KEYS bitmasks), so the pointer model,
    keyboard, calibration and API work unchanged."""

    def __init__(
        self,
        name: str = DEFAULT_NAME,
        *,
        configfs: str = CONFIGFS,
        sysfs: str = "/sys",
        dev: str = "/dev",
        timeout: float = p.REPLY_TIMEOUT_S,
        wait_ack: bool = True,
        trace: Callable[..., None] | None = None,
        nodes: dict[str, str] | None = None,
        udc: str | None = None,
        open_node: Callable[..., HidgNode] = HidgNode,
    ):
        self.name = name
        self.port = f"gadget:{name}"
        self.baud = 0
        self.timeout = timeout
        self.wait_ack = wait_ack
        self.stats: Counter[str] = Counter()
        self.async_errors: list = []  # (never filled: a gadget has no error replies, only timeouts)
        self._trace = trace
        self._sysfs = sysfs
        self._io = threading.RLock()
        self._leds = 0
        self.udc = udc if udc is not None else bound_udc(name, configfs)
        paths = nodes if nodes is not None else gadget_nodes(name, configfs, sysfs, dev)
        if not paths:
            raise HidPortError(f"no USB gadget {name!r} with HID functions on this board: set it up with "
                               "`sudo ihc gadget up` (or `sudo python3 tools/gadget.py up`)")
        self._nodes: dict[str, HidgNode] = {}
        try:
            for fn, path in paths.items():
                self._nodes[fn] = open_node(path, read=FUNCTIONS[fn].out_reports)
        except OSError as e:
            self.close()
            hint = " (permission: `ihc gadget up` gives the nodes to the user who ran it with sudo, or --owner)" \
                if e.errno == errno.EACCES else ""
            raise HidPortError(f"cannot open {e.filename or path}: {e.strerror or e}{hint}") from e

    # -- lifecycle ---------------------------------------------------------------------------------

    def __enter__(self) -> GadgetBackend:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        with self._io:
            for node in self._nodes.values():
                node.close()

    @property
    def functions(self) -> list[str]:
        return list(self._nodes)

    # -- HidBackend --------------------------------------------------------------------------------

    def udc_state(self) -> str | None:
        return udc_state(self.udc, self._sysfs)

    def info(self) -> dict:
        """The CH9329 GET_INFO fields: whether the phone enumerated the gadget, keyboard LEDs."""
        with self._io:
            state = self.udc_state()
            kb = self._nodes.get("keyboard")
            if kb is not None:
                for report in kb.read_all():
                    self._leds = report[-1]
            connected = state == "configured"
            return {
                "version": f"Linux USB gadget on {self.udc or '?'}",
                "version_raw": 0,
                "usb_connected": connected,
                "usb_status": 1 if connected else 0,
                "usb_state": state,
                "num_lock": bool(self._leds & 0x01),
                "caps_lock": bool(self._leds & 0x02),
                "scroll_lock": bool(self._leds & 0x04),
                "raw": f"udc={self.udc} state={state} leds={self._leds:#04x}",
                "gadget": {"name": self.name, "udc": self.udc, "functions": self.functions},
            }

    def keyboard(self, modifiers: int, keys: Sequence[int]) -> None:
        if len(keys) > 6:
            raise p.FrameError("at most 6 simultaneous keys")
        keys = list(keys) + [0] * (6 - len(keys))
        self._send("keyboard", bytes([modifiers & 0xFF, 0, *keys]), p.Cmd.SEND_KB_GENERAL_DATA)

    def media(self, code: int) -> None:
        """Multimedia keys held down, as a protocol.MEDIA_KEYS bitmask; 0 releases."""
        if not 0 <= code <= 0xFFFFFF:
            raise p.FrameError(f"media bitmap out of range: {code:#x}")
        self._send("consumer", code.to_bytes(3, "little"), p.Cmd.SEND_KB_MEDIA_DATA)

    def acpi(self, bits: int) -> None:
        """ACPI keys held down, as a protocol.ACPI_KEYS bitmask; 0 releases."""
        if not 0 <= bits <= 0x07:
            raise p.FrameError(f"ACPI bitmap out of range: {bits:#x}")
        self._send("system", bytes([bits]), p.Cmd.SEND_KB_MEDIA_DATA)

    def mouse_rel(self, dx: int, dy: int, buttons: int = 0, wheel: int = 0) -> None:
        self._send("mouse", bytes([buttons & 0x07, _i8(dx), _i8(dy), _i8(wheel)]), p.Cmd.SEND_MS_REL_DATA)

    def mouse_abs(self, x: int, y: int, buttons: int = 0, wheel: int = 0) -> None:
        """Absolute pointer on the CH9329's 4096 x 4096 grid, sent scaled to 0..32767."""
        for axis, v in (("x", x), ("y", y)):
            if not 0 <= v <= 4095:
                raise p.FrameError(f"absolute {axis} out of range 0..4095: {v}")
        ax, ay = scale_abs(x), scale_abs(y)
        report = bytes([buttons & 0x07, *ax.to_bytes(2, "little"), *ay.to_bytes(2, "little"), _i8(wheel)])
        self._send("absolute", report, p.Cmd.SEND_MS_ABS_DATA)

    def release_all(self, attempts: int = 3, retry_wait: float = 0.03) -> None:
        """Release every key, media/power key and relative mouse button (absolute buttons: see
        PointerModel.release_all). Every release is tried; the first failure is raised after."""
        steps = (lambda: self.keyboard(0, []), lambda: self.media(0), lambda: self.acpi(0),
                 lambda: self.mouse_rel(0, 0, 0, 0))
        failed: HidError | None = None
        for step in steps:
            for attempt in range(attempts):
                try:
                    step()
                    break
                except HidPortError:
                    raise
                except HidStatusError as e:
                    if e.status == p.Status.BAD_PARAM:  # no such interface in this profile: nothing held
                        break
                    err = e
                except HidError as e:
                    err = e
                if attempt == attempts - 1:
                    failed = failed or err
                else:
                    time.sleep(retry_wait)
        if failed is not None:
            raise failed

    def sync(self) -> None:
        """Wait until every queued report was taken; the ones that were not count as lost acks."""
        with self._io:
            for node in self._nodes.values():
                self._delivered(node)

    def leds(self) -> int:
        """The phone's keyboard LED bits (bit0 Num, bit1 Caps, bit2 Scroll Lock), newest first read."""
        self.info()
        return self._leds

    # -- plumbing ----------------------------------------------------------------------------------

    def _delivered(self, node: HidgNode) -> bool:
        """Whether the report in flight on `node` (if any) was taken by the phone."""
        if not node.pending:
            return True
        try:
            taken = node.writable(self.timeout)
        except OSError as e:
            raise HidPortError(f"{node.path}: {e.strerror or e}") from e
        if taken:
            node.pending = False
            if node.lost:  # it went out after all, later than we waited for
                node.lost = False
                self.stats["late_replies"] += 1
                return True
            # A disconnect also completes the transfer (with an error): only a phone that is still
            # there took it.
            state = self.udc_state()
            if state is not None and state != "configured":
                self.stats["lost_acks"] += 1
                self._emit("lost", path=node.path, state=state)
                return False
            self.stats["delivered"] += 1
            return True
        if not node.lost:
            node.lost = True
            self.stats["lost_acks"] += 1
            self._emit("lost", path=node.path, state=self.udc_state())
        return False

    def _send(self, fn: str, report: bytes, cmd: int) -> None:
        with self._io:
            node = self._nodes.get(fn)
            if node is None:
                raise HidStatusError(f"this gadget has no {fn} interface (functions: {', '.join(self._nodes)}); "
                                     "set it up with a profile that has one", cmd, p.Status.BAD_PARAM)
            if not self._delivered(node):  # the previous report on this interface is still waiting
                self.stats["timeouts"] += 1
                raise HidTimeout(self._not_taken(fn))
            t0 = time.monotonic()
            try:
                node.write(report)
            except BlockingIOError:
                self.stats["timeouts"] += 1
                raise HidTimeout(self._not_taken(fn)) from None
            except OSError as e:
                if e.errno in _NOT_CONNECTED:
                    self.stats["not_connected"] += 1
                    raise HidStatusError(f"{fn} report not sent: the phone has not enumerated the gadget "
                                         f"(USB state: {self.udc_state() or 'unknown'}); is it unlocked, the "
                                         "accessory allowed, the cable in?", cmd, p.Status.EXEC_ERROR) from e
                raise HidPortError(f"{node.path}: {e.strerror or e}") from e
            node.pending, node.lost = True, False
            self.stats["tx"] += 1
            if self.wait_ack:
                if not self._delivered(node):
                    self.stats["timeouts"] += 1
                    raise HidTimeout(self._not_taken(fn))
                self._emit("tx", fn=fn, data=report.hex(" "), delivered_ms=round((time.monotonic() - t0) * 1000, 2))
            else:
                self._emit("tx", fn=fn, data=report.hex(" "))

    def _not_taken(self, fn: str) -> str:
        return (f"the phone did not take the {fn} report within {self.timeout * 1000:.0f} ms (USB state: "
                f"{self.udc_state() or 'unknown'}): locked, asleep, unplugged, or the accessory not allowed?")

    def _emit(self, event: str, **fields) -> None:
        if self._trace:
            self._trace(f"gadget_{event}", **fields)


def scale_abs(grid: int) -> int:
    """CH9329 grid 0..4095 -> HID 0..32767, rounded to nearest (as the bridge firmware does)."""
    return (grid * ABS_MAX + 2047) // 4095


def _i8(v: int) -> int:
    if not -127 <= v <= 127:
        raise p.FrameError(f"value out of int8 range: {v}")
    return v & 0xFF
