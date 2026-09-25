"""Why a board shows no HDMI input: read what the device tree, the kernel and the boot configuration
say, offline and without v4l-utils (`ihc-capture-check list` prints this when it finds no hdmirx
node).

An SoC HDMI receiver (the Orange Pi 5 Plus's rk_hdmirx, mainline snps_hdmirx) needs three things: a
device tree node with status "okay", a driver in the kernel (built in or a module), and the driver
bound to the node. Any of the three can be missing on a given board image.
"""

from __future__ import annotations

import gzip
import os
from pathlib import Path

WORDS = ("hdmirx", "hdmi_receiver", "hdmi-receiver", "hdmi-rx", "hdmi_rx", "hdmiin", "hdmi-in")


def _matches(name: str) -> bool:
    name = name.lower()
    return any(w in name for w in WORDS)


def _prop(node: Path, prop: str) -> list[str]:
    try:
        raw = (node / prop).read_bytes()
    except OSError:
        return []
    return [s.decode(errors="replace") for s in raw.split(b"\0") if s]


def dt_nodes(root: Path) -> list[tuple[str, str, str]]:
    """(node path, status, compatible) of the HDMI receiver nodes and their reserved memory."""
    base = root / "sys" / "firmware" / "devicetree" / "base"
    out = []
    for parent in (base, base / "reserved-memory"):
        for node in sorted(parent.iterdir()) if parent.is_dir() else []:
            if node.is_dir() and _matches(node.name):
                status = (_prop(node, "status") or ["okay"])[0]  # no status property = enabled
                out.append((str(node.relative_to(base)), status, ", ".join(_prop(node, "compatible"))))
    return out


def bound_drivers(root: Path) -> dict[str, list[str]]:
    """Platform driver name -> the devices bound to it, for HDMI receiver drivers."""
    drivers = root / "sys" / "bus" / "platform" / "drivers"
    out = {}
    for d in sorted(drivers.iterdir()) if drivers.is_dir() else []:
        if _matches(d.name):
            out[d.name] = sorted(e.name for e in d.iterdir() if e.is_symlink() and e.name != "module")
    return out


def kernel_config(root: Path, release: str) -> list[str]:
    """HDMIRX lines of the kernel configuration (/proc/config.gz, else /boot/config-<release>)."""
    lines: list[str] = []
    try:
        with gzip.open(root / "proc" / "config.gz", "rt") as f:
            lines = f.read().splitlines()
    except OSError:
        try:
            lines = (root / "boot" / f"config-{release}").read_text(errors="replace").splitlines()
        except OSError:
            return []
    return [line for line in lines if "HDMIRX" in line.upper()]


def modules(root: Path, release: str) -> tuple[list[str], list[str]]:
    """(loadable module files, built-in modules) of HDMI receiver drivers for this kernel."""
    mods = root / "lib" / "modules" / release
    files = sorted(str(p.relative_to(mods)) for p in mods.rglob("*.ko*") if _matches(p.name)) if mods.is_dir() else []
    try:
        builtin = [line.strip() for line in (mods / "modules.builtin").read_text().splitlines() if _matches(line)]
    except OSError:
        builtin = []
    return files, builtin


def loaded_modules(root: Path) -> list[str]:
    try:
        return [line.split()[0] for line in (root / "proc" / "modules").read_text().splitlines()
                if line.strip() and _matches(line.split()[0])]
    except OSError:
        return []


def boot_overlays(root: Path) -> tuple[list[str], list[str]]:
    """(HDMI-input overlays shipped in /boot, the overlay lines of the boot environment)."""
    shipped = []
    for d in (root / "boot" / "dtb" / "rockchip" / "overlay", root / "boot" / "dtb" / "overlay"):
        shipped += sorted(str(p.relative_to(root)) for p in d.glob("*") if _matches(p.name)) if d.is_dir() else []
    env = []
    for name in ("armbianEnv.txt", "orangepiEnv.txt"):
        try:
            text = (root / "boot" / name).read_text(errors="replace")
        except OSError:
            continue
        env += [f"{name}: {line.strip()}" for line in text.splitlines() if "overlay" in line.lower()]
    return shipped, env


def diagnose(root: str = "/", release: str | None = None) -> list[str]:
    """Readable findings, then a verdict line starting with '=> '."""
    r = Path(root)
    release = release or os.uname().release
    out = [f"kernel {release}"]
    nodes = dt_nodes(r)
    for path, status, compatible in nodes:
        out.append(f"device tree: {path} status={status}" + (f" ({compatible})" if compatible else ""))
    if not nodes:
        out.append("device tree: no HDMI receiver node")
    drivers = bound_drivers(r)
    for name, devs in drivers.items():
        out.append(f"driver {name}: " + (f"bound to {', '.join(devs)}" if devs else "loaded, bound to nothing"))
    files, builtin = modules(r, release)
    loaded = loaded_modules(r)
    for f in files:
        out.append(f"module file: {f}" + (" (loaded)" if Path(f).name.split(".")[0] in loaded else ""))
    for b in builtin:
        out.append(f"built into the kernel: {b}")
    config = kernel_config(r, release)
    for line in config:
        out.append(f"kernel config: {line}")
    shipped, env = boot_overlays(r)
    for s in shipped:
        out.append(f"overlay available: {s}")
    for e in env:
        out.append(f"boot config: {e}")

    if not (r / "sys" / "firmware" / "devicetree" / "base").is_dir():
        return out + ["=> this machine has no device tree, so no SoC HDMI input: use a USB capture card"]
    enabled = [n for n in nodes if n[1] in ("okay", "ok") and not n[0].startswith("reserved-memory")]
    receivers = [n for n in nodes if not n[0].startswith("reserved-memory")]
    has_driver = bool(drivers or files or builtin or any(line.endswith(("=y", "=m")) for line in config))
    if not receivers:
        verdict = ("this kernel's device tree does not describe the HDMI input: use a board image made for "
                   "HDMI input (Orange Pi's own image enables it)")
    elif not enabled:
        verdict = ("the device tree has the HDMI input but leaves it off (status disabled): it needs a device "
                   "tree overlay that turns it on, or a board image that enables it (Orange Pi's own image)")
    elif not has_driver:
        verdict = "the HDMI input is on in the device tree, but this kernel has no driver for it"
    elif any(devs for devs in drivers.values()):
        verdict = ("the driver is bound but made no video device: its start-up failed; "
                   "see `sudo dmesg | grep -i hdmirx`")
    elif files and not drivers:
        mod = Path(files[0]).name.split(".")[0]
        verdict = f"the driver is a module that is not loaded: try `sudo modprobe {mod}`, then list again"
    else:
        verdict = "the driver did not take the HDMI input: see `sudo dmesg | grep -i hdmirx`"
    return out + [f"=> {verdict}"]
