"""Device registry: builds IPhoneDevice objects from a farm config or from simulators.

Config (TOML):

    [[device]]
    id = "iphone-01"
    model = "iPhone 15"
    hid = { port = "/dev/serial/by-path/...-port0", baud = 9600 }
    video = { device = "/dev/v4l/by-path/...-video-index0", size = "1920x1080", fps = 30 }
    calibration = "calib/iphone-01.json"

Physical paths (/dev/serial/by-path, /dev/v4l/by-path) name USB ports, not enumeration order, so a
phone keeps its id across reboots as long as its cables stay in the same ports. `discover()` pairs
serial and video devices that hang off the same USB hub, which is how one rig is cabled.
"""

from __future__ import annotations

import os
import re
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .device import DeviceInfo, IPhoneDevice
from .hid.ch9329 import CH9329Backend
from .input.pointer import PointerCalibration
from .models import MODELS
from .video.geometry import ScreenRect

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib


class Registry:
    def __init__(self) -> None:
        self._devices: dict[str, IPhoneDevice] = {}
        self._extras: dict[str, object] = {}  # e.g. SimRig per simulated device
        self._lock = threading.Lock()

    def add(self, device: IPhoneDevice, extra: object | None = None) -> None:
        with self._lock:
            if device.id in self._devices:
                raise ValueError(f"duplicate device id {device.id!r}")
            self._devices[device.id] = device
            if extra is not None:
                self._extras[device.id] = extra

    def get(self, device_id: str) -> IPhoneDevice:
        try:
            return self._devices[device_id]
        except KeyError:
            raise KeyError(f"no device {device_id!r}") from None

    def extra(self, device_id: str):
        return self._extras.get(device_id)

    def devices(self) -> list[IPhoneDevice]:
        return list(self._devices.values())

    def start_monitors(self, interval: float = 2.0) -> None:
        for d in self.devices():
            d.check()
            d.start_monitor(interval)

    def close(self) -> None:
        for d in self.devices():
            try:
                d.close()
            except Exception:
                pass


def load_config(path: str | Path, log=None) -> Registry:
    """Hardware devices from a TOML file. Import of the capture layer is deferred so HID-only
    setups do not need OpenCV at import time."""
    from .video.capture import V4L2Capture

    cfg = tomllib.loads(Path(path).read_text())
    base = Path(path).resolve().parent
    reg = Registry()
    for entry in cfg.get("device", []):
        dev_id = entry["id"]
        hid_cfg = entry["hid"]
        port, baud = hid_cfg["port"], int(hid_cfg.get("baud", 9600))
        addr = int(hid_cfg.get("addr", 0))

        def open_hid(port=port, baud=baud, addr=addr) -> CH9329Backend:
            return CH9329Backend(port, baud, addr=addr, trace=None)

        video = entry["video"]
        w, h = (int(v) for v in str(video.get("size", "1920x1080")).lower().split("x"))
        source = V4L2Capture(video["device"], width=w, height=h, fps=int(video.get("fps", 30)),
                             fourcc=video.get("fourcc", "MJPG"))
        calib = entry.get("calibration")
        model = MODELS.get(entry.get("model", ""))
        rect = entry.get("screen_rect")  # [x, y, w, h] in frame pixels, when geometry is not enough
        device = IPhoneDevice(
            DeviceInfo(dev_id, model.name if model else entry.get("model", ""), "hardware"),
            open_hid(),
            source,
            calibration=None if not model else _initial_calibration(base / calib if calib else None, model),
            calibration_path=(base / calib) if calib else None,
            screen_rect=ScreenRect(*map(float, rect)) if rect else None,
            reopen_hid=open_hid,
            log=log,
        )
        reg.add(device)
    return reg


def _initial_calibration(path: Path | None, model) -> PointerCalibration | None:
    if path is not None and path.exists():
        return PointerCalibration.load(path)
    return PointerCalibration(screen_pt=(float(model.width_pt), float(model.height_pt)))


def simulated(count: int = 1, *, model: str = "iphone-15", calibrated: bool = True, log=None, **rig_options) -> Registry:
    """`count` simulated phones behind the real driver stack. With `calibrated`, each starts with the
    exact calibration of its simulated pointer; otherwise with the uncalibrated default and the
    Safari calibration page available (open it with device.calibrate(page_url))."""
    from .sim.rig import exact_calibration, make_rig

    reg = Registry()
    for i in range(1, count + 1):
        rig = make_rig(f"sim-{i:02d}", model, **rig_options)
        m = rig.phone.model
        cal = exact_calibration(rig.chip.pointer) if calibrated else PointerCalibration(screen_pt=(m.width_pt, m.height_pt))
        device = IPhoneDevice(DeviceInfo(rig.id, m.name, "sim"), rig.hid, rig.capture, calibration=cal, log=log)
        rig.phone.web_listeners.append(lambda url, event, d=device: d.clicks.push(event))
        reg.add(device, rig)
    return reg


@dataclass
class Rig:
    """A serial port and a video device found under the same USB hub."""

    usb_path: str
    serial: str
    video: str


def _hub_of(by_path_name: str) -> str | None:
    """'pci-0000:00:14.0-usb-0:1.2:1.0-port0' -> 'pci-0000:00:14.0-usb-0:1' (the hub the device
    hangs off: drop the last port number and the interface)."""
    m = re.match(r"(.*-usb[v0-9]*-[0-9]+:)([0-9.]+)(:[0-9.]+)?", by_path_name)
    if not m:
        return None
    ports = m.group(2).split(".")
    return m.group(1) + ".".join(ports[:-1]) if len(ports) > 1 else None


def discover(serial_dir: str = "/dev/serial/by-path", video_dir: str = "/dev/v4l/by-path") -> list[Rig]:
    """Pair serial ports and video capture nodes that share a host-side USB hub.

    Works with the cabling convention "one small USB hub on the host per phone, carrying that
    phone's USB-serial cable and capture card". Rigs cabled straight into host ports cannot be
    paired automatically; write their config by hand."""

    def listing(d: str, want) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        if os.path.isdir(d):
            for name in sorted(os.listdir(d)):
                hub = _hub_of(name)
                if hub and want(name):
                    out.setdefault(hub, []).append(os.path.join(d, name))
        return out

    serials = listing(serial_dir, lambda n: True)
    videos = listing(video_dir, lambda n: n.endswith("video-index0"))
    rigs = []
    for hub in sorted(set(serials) & set(videos)):
        if len(serials[hub]) == 1 and len(videos[hub]) == 1:
            rigs.append(Rig(hub, serials[hub][0], videos[hub][0]))
    return rigs


def config_from_discovery(rigs: list[Rig], model: str = "") -> str:
    """A starting farm.toml for the rigs found by discover()."""
    lines = []
    for i, r in enumerate(rigs, 1):
        lines += [
            "[[device]]",
            f'id = "iphone-{i:02d}"',
            f'model = "{model}"',
            f'hid = {{ port = "{r.serial}", baud = 9600 }}',
            f'video = {{ device = "{r.video}", size = "1920x1080", fps = 30 }}',
            f'calibration = "calib/iphone-{i:02d}.json"',
            "",
        ]
    return "\n".join(lines)


def default_state_dir() -> Path:
    """Where a box keeps per-rig calibration: $IHC_STATE_DIR or ~/.local/share/ihc."""
    return Path(os.environ.get("IHC_STATE_DIR") or Path.home() / ".local" / "share" / "ihc")


class NoVideo:
    """FrameSource for a rig without a capture card: HID works, video reports no signal."""

    def __init__(self, size: tuple[int, int] = (1920, 1080)):
        self.size = size

    def latest(self, newer_than: int = -1, timeout: float = 1.0):
        time.sleep(min(timeout, 0.05))
        raise TimeoutError("no capture card is paired with this rig")

    def close(self) -> None:
        pass


def auto(
    *,
    state_dir: str | Path | None = None,
    log=None,
    ports: list[str] | None = None,
    sysfs: str = "/sys",
    video_size: tuple[int, int] = (1920, 1080),
    fps: int = 30,
    probe_timeout: float = 0.25,
    open_video=None,
) -> Registry:
    """Zero-config registry: every rig found on this host (see ihc.rigs), calibration files kept
    in `state_dir` under the rig id."""
    from .rigs import find_rigs

    state = Path(state_dir) if state_dir else default_state_dir()
    if open_video is None:
        from .video.capture import V4L2Capture

        def open_video(device: str):
            return V4L2Capture(device, width=video_size[0], height=video_size[1], fps=fps)

    reg = AutoRegistry(lambda ports_in_use, videos_in_use: find_rigs(ports, sysfs=sysfs, timeout=probe_timeout, log=log,
                                                                   skip=ports_in_use, skip_videos=videos_in_use),
                       lambda spec: _build_rig(spec, state, open_video, video_size, log))
    reg.rescan()
    return reg


class AutoRegistry(Registry):
    """A registry that keeps looking for newly plugged rigs (rescan / start_rescan)."""

    def __init__(self, find, build):
        super().__init__()
        self._find = find
        self._build = build
        self._stop_scan = threading.Event()
        self._scanner: threading.Thread | None = None

    def rescan(self) -> list[str]:
        """Probe serial ports not in use yet; add the rigs found. Returns the new device ids."""
        ports = {os.path.realpath(d.hid.port) for d in self.devices()}
        videos = {os.path.realpath(str(d.source.device)) for d in self.devices() if hasattr(d.source, "device")}
        added = []
        for spec in self._find(ports, videos):
            if spec.id in self._devices:
                continue
            device = self._build(spec)
            self.add(device, spec)
            added.append(device.id)
        return added

    def start_rescan(self, interval: float = 10.0, on_added=None) -> None:
        def run() -> None:
            while not self._stop_scan.wait(interval):
                try:
                    for dev_id in self.rescan():
                        if on_added:
                            on_added(self.get(dev_id))
                except Exception:
                    pass  # a failing probe must not stop hot-plug detection

        self._scanner = threading.Thread(target=run, name="rig-rescan", daemon=True)
        self._scanner.start()

    def close(self) -> None:
        self._stop_scan.set()
        if self._scanner is not None:
            self._scanner.join(timeout=5)
        super().close()


def _build_rig(spec, state: Path, open_video, video_size, log) -> IPhoneDevice:
    c = spec.chip

    def open_hid(port=c.port, baud=c.baud, addr=c.addr) -> CH9329Backend:
        return CH9329Backend(port, baud, addr=addr)

    cal_path = state / f"{spec.id}.json"
    device = IPhoneDevice(
        DeviceInfo(spec.id, "", "hardware"),
        open_hid(),
        open_video(spec.video.device) if spec.video else NoVideo(video_size),
        calibration=PointerCalibration.load(cal_path) if cal_path.exists() else None,
        calibration_path=cal_path,
        reopen_hid=open_hid,
        log=log,
    )
    if log:
        log("rig_found", id=spec.id, port=c.port, baud=c.baud, chip=c.info.get("version"),
            video=spec.video.device if spec.video else None, notes=spec.notes)
    return device
