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
import time
from pathlib import Path

WORDS = ("hdmirx", "hdmi_receiver", "hdmi-receiver", "hdmi-rx", "hdmi_rx", "hdmiin", "hdmi-in")
# device tree nodes of the receiver itself (not e.g. the Orange Pi 5 Plus's always-on hdmiin-sound card)
NODE_WORDS = ("hdmirx", "hdmi_receiver", "hdmi-receiver")


def _matches(name: str, words: tuple[str, ...] = WORDS) -> bool:
    name = name.lower()
    return any(w in name for w in words) and "sound" not in name


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
            if node.is_dir() and _matches(node.name, NODE_WORDS):
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


def boot_env(root: Path) -> tuple[Path | None, dict[str, str]]:
    """The boot environment file (Armbian, Orange Pi) and its key=value settings."""
    for name in ("armbianEnv.txt", "orangepiEnv.txt"):
        path = root / "boot" / name
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        settings = {}
        for line in text.splitlines():
            key, sep, value = line.partition("=")
            if sep:
                settings[key.strip()] = value.strip()
        return path, settings
    return None, {}


def boot_time(root: Path) -> float | None:
    try:
        return time.time() - float((root / "proc" / "uptime").read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def overlay_advice(root: Path, dtbo: str) -> tuple[list[str], str]:
    """(findings, verdict) on getting the boot script to apply the overlay `dtbo` (a path relative
    to root).

    Armbian's boot script loads each overlays= entry as overlay/<overlay_prefix>-<entry>.dtbo; since
    Armbian 24.11 it also tries overlay/<entry>.dtbo. On an older script a kernel overlay whose name
    does not start with the prefix (rk3588-hdmirx.dtbo with overlay_prefix=rockchip-rk3588) cannot
    be named in overlays= at all: it is skipped without a word. user_overlays= (files in
    /boot/overlay-user) works with every version."""
    name = Path(dtbo).name.removesuffix(".dtbo")
    overlay_dir = root / Path(dtbo).parent
    env_path, env = boot_env(root)
    env_file = f"/boot/{env_path.name if env_path else 'armbianEnv.txt'}"
    prefix = env.get("overlay_prefix", "")
    listed, user_listed = env.get("overlays", "").split(), env.get("user_overlays", "").split()
    try:
        script = (root / "boot" / "boot.cmd").read_text(errors="replace")
    except OSError:
        script = ""
    plain = "overlay/${overlay_file}.dtbo" in script  # the Armbian 24.11+ fallback

    def found(entry: str) -> bool:
        return (overlay_dir / f"{prefix}-{entry}.dtbo").is_file() or (plain and (overlay_dir / f"{entry}.dtbo").is_file())

    findings = []
    if script and listed:
        skipped = [e for e in listed if not found(e)]
        if skipped:
            findings.append(f"boot script: skips these overlays= entries, no such file for it: {' '.join(skipped)}")
    if prefix and name.startswith(prefix + "-"):
        entry: str | None = name[len(prefix) + 1:]
    else:
        entry = name if plain else None  # (no boot.cmd to read: do not count on the fallback)
    user_file = root / "boot" / "overlay-user" / f"{name}.dtbo"
    user_steps = (f"use user_overlays, which every boot script version loads: `sudo mkdir -p /boot/overlay-user && "
                  f"sudo cp /{dtbo} /boot/overlay-user/`, add the line user_overlays={name} to {env_file} "
                  f"(or add {name} to that line if there is one)"
                  + (f", remove {name} from overlays=" if name in listed else "") + ", then reboot")
    if (entry is not None and entry in listed) or (name in user_listed and user_file.is_file()):
        booted = boot_time(root)
        try:
            edited = env_path.stat().st_mtime if env_path else None
        except OSError:
            edited = None
        if booted is not None and edited is not None and edited > booted:
            return findings, f"{env_file} names the overlay but changed after this boot: reboot"
        return findings, (f"{env_file} names the overlay but this boot did not apply it: when one overlay fails to "
                          f"apply, the boot script drops them all (its messages are on the serial console); try "
                          f"with this overlay alone")
    if name in listed or name in user_listed:
        reason = (f"this boot script looks for /boot/{Path(dtbo).parent.relative_to('boot')}/{prefix}-{name}.dtbo, "
                  f"which does not exist, so it skips {name}" if name in listed
                  else f"user_overlays names {name} but /boot/overlay-user/{name}.dtbo does not exist")
        return findings, f"{reason}: {user_steps}"
    if entry is not None:
        return findings, (f"the device tree has the HDMI input but leaves it off; this image ships the overlay that "
                          f"turns it on: add {entry} to the overlays= line of {env_file} (keep what is there, "
                          f"separate with a space), then reboot")
    return findings, f"the device tree has the HDMI input but leaves it off; this image ships the overlay: {user_steps}"


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
    elif not enabled and shipped:
        findings, verdict = overlay_advice(r, shipped[0])
        out += findings
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
