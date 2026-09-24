"""Typing and key combos on top of a HidBackend (US layout, see keymap).

A keyboard report carries the whole key state, so resending one is harmless: after an ambiguous
failure (timeout, or a frame the chip rejected as damaged) the same report is sent again, and keys
are always released even when something fails half-way.
"""

from __future__ import annotations

import time
from typing import Callable, Sequence

from ..hid.base import HidBackend, HidStatusError, HidTimeout
from . import keymap
from .pointer import NOT_EXECUTED


class Keyboard:
    def __init__(self, hid: HidBackend, *, hold: float = 0.02, gap: float = 0.02, retries: int = 2,
                 sleep: Callable[[float], None] = time.sleep):
        self.hid = hid
        self.hold = hold
        self.gap = gap
        self.retries = retries
        self.resends = 0
        self._sleep = sleep

    def report(self, mods: int, keys: Sequence[int]) -> None:
        """Send one key-state report, resending it after an ambiguous failure."""
        for attempt in range(self.retries + 1):
            try:
                self.hid.keyboard(mods, keys)
                return
            except HidTimeout:
                if attempt == self.retries:
                    raise
            except HidStatusError as e:
                if e.status not in NOT_EXECUTED or attempt == self.retries:
                    raise
            self.resends += 1

    def type(self, text: str) -> None:
        """Type ASCII text; raises ValueError before sending anything if a character is not typeable."""
        reports = keymap.text_reports(text)
        try:
            for mods, keys in reports:
                self.report(mods, keys)
                self._sleep(self.hold if keys else self.gap)
        finally:
            self._release()

    def key(self, combo: str, hold: float = 0.05) -> None:
        mods, keys = keymap.parse_combo(combo)
        try:
            self.report(mods, keys)
            self._sleep(hold)
        finally:
            self._release()
        self._sleep(self.gap)

    def _release(self) -> None:
        try:
            self.report(0, [])
        except (HidTimeout, HidStatusError):
            pass
