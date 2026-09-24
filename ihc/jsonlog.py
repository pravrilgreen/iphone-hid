"""JSON-lines event log: one object per line with a wall-clock `ts`."""

from __future__ import annotations

import json
import time
from pathlib import Path


class EventLog:
    """Callable as log(event, **fields); usable directly as a CH9329Backend trace hook."""

    def __init__(self, path: str | Path | None):
        self.path = Path(path) if path else None
        self._f = None
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._f = open(self.path, "a", buffering=1, encoding="utf-8")

    def __call__(self, event: str, **fields) -> None:
        if self._f is None:
            return
        record = {"ts": round(time.time(), 6), "event": event, **fields}
        self._f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def close(self) -> None:
        if self._f is not None:
            self._f.close()
            self._f = None
