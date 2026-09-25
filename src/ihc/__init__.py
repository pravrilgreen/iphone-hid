"""iphone-hid: control iPhones through external hardware only (HDMI or USB-C video in, USB HID out)."""

from __future__ import annotations

import re
from pathlib import Path


def _version() -> str:
    """The version, from one source: the bundle's VERSION file, a source checkout's pyproject.toml, or
    the installed distribution's metadata."""
    root = Path(__file__).resolve().parents[2]  # the bundle's /opt/ihc, or a checkout's top directory
    try:
        return (root / "VERSION").read_text().strip()
    except OSError:
        pass
    try:
        m = re.search(r'^version = "([^"]+)"', (root / "pyproject.toml").read_text(), re.M)
        if m:
            return m.group(1)
    except OSError:
        pass
    try:
        from importlib.metadata import version

        return version("iphone-hid")
    except Exception:  # not installed either
        return "0+unknown"


__version__ = _version()
