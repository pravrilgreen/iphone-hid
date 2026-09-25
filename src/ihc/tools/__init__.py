"""Lab tools installed as commands: `ihc-hidtest` (keyboard and mouse hardware, by hand),
`ihc-capture-check` (video inputs) and `ihc-hid-loopback` (reports seen by a Linux host)."""

from __future__ import annotations

import os
from pathlib import Path


def log_dir() -> Path:
    """Where the tools write their logs: $IHC_TEST_LOG_DIR; else docs/test-logs in a source checkout,
    so results can be committed with the checklist; else ~/ihc-test-logs."""
    env = os.environ.get("IHC_TEST_LOG_DIR")
    if env:
        return Path(env)
    checkout = Path(__file__).resolve().parents[3] / "docs" / "test-logs"
    if checkout.is_dir():
        return checkout
    return Path.home() / "ihc-test-logs"
