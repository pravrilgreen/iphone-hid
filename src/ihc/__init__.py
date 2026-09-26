"""iphone-hid SDK: drive the iPhone of each box (touch, keys, buttons, screenshots) from Python."""

from __future__ import annotations

from pathlib import Path


def _version() -> str:
    """The repository's VERSION file in a checkout, else the installed distribution's version."""
    try:
        return (Path(__file__).resolve().parents[2] / "VERSION").read_text().strip()
    except OSError:
        pass
    try:
        from importlib.metadata import version

        return version("iphone-hid")
    except Exception:  # not installed either
        return "0+unknown"


__version__ = _version()

from .client import Farm, IhcError, Phone  # noqa: E402

__all__ = ["Farm", "IhcError", "Phone", "__version__"]
