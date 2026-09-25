"""Typing and key combos on top of a HidBackend (US layout, see keymap).

A keyboard report carries the whole key state, so resending one is harmless: after an ambiguous
failure (timeout, or a frame the chip rejected as damaged) the same report is sent again, and keys
are always released even when something fails half-way.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Callable, Sequence

from ..hid import protocol as p
from ..hid.base import HidBackend, HidError, HidStatusError, HidTimeout
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
        self.typed = 0  # characters of the last type() call that went out completely
        self._sleep = sleep

    def report(self, mods: int, keys: Sequence[int]) -> None:
        """Send one key-state report, resending it after an ambiguous failure. A release is also
        resent when the device could not deliver it (E6): a key left down would auto-repeat into
        the app."""
        release = not mods and not keys
        for attempt in range(self.retries + 1):
            try:
                self.hid.keyboard(mods, keys)
                return
            except HidTimeout:
                if attempt == self.retries:
                    raise
            except HidStatusError as e:
                retry = e.status in NOT_EXECUTED or (release and e.status == p.Status.EXEC_ERROR)
                if not retry or attempt == self.retries:
                    raise
                if e.status == p.Status.EXEC_ERROR:
                    self._sleep(0.03)
            self.resends += 1

    def type(self, text: str) -> None:
        """Type ASCII text; raises ValueError before sending anything if a character is not typeable.
        After a failure, `typed` tells how many characters went out (to resume, not retype)."""
        reports = keymap.text_reports(text)
        self.typed = 0
        with self._released():
            for mods, keys in reports:
                self.report(mods, keys)
                if not keys:  # every character ends with a release report
                    self.typed += 1
                self._sleep(self.hold if keys else self.gap)

    def key(self, combo: str, hold: float = 0.05) -> None:
        mods, keys = keymap.parse_combo(combo)
        with self._released():
            self.report(mods, keys)
            self._sleep(hold)
        self._sleep(self.gap)

    @contextmanager
    def _released(self):
        """Always end with every key up. A failed final release is raised (a key may be held);
        after another failure it is attempted quietly and the first error wins."""
        try:
            yield
        except BaseException:
            try:
                self.report(0, [])
            except HidError:
                pass
            raise
        self.report(0, [])
