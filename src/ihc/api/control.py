"""Live remote control (KVM style) over a WebSocket: ordered, coalesced HID reports.

Browser input is applied strictly in arrival order by one worker thread per connection, so a click
is never reordered with the moves around it:
- relative moves and wheel accumulate while a report is on the wire and go out as at most one
  report per tick; sums beyond +-127 are split over several reports, so the total movement is
  exactly what the operator's mouse produced;
- a button change first flushes the pending movement (with the old buttons), then goes out at once
  as its own report: a press lands where the pointer is, and press/release pairs are never merged
  or lost;
- absolute moves keep only the newest position (one report per tick), a button change goes out at
  once; key-state reports and actions go out one by one, never merged;
- the op queue is bounded: when it is full, the socket is not read (backpressure);
- input that waited more than STALE seconds for the device (a REST action, a calibration held it)
  is dropped instead of being replayed late onto whatever screen shows by then: motion, wheel,
  button and key presses; releases always go out (in order), so nothing stays held. The client
  gets a {"t": "dropped"} message;
- when the connection ends, everything is released.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import time
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from . import actions

TICK = 0.016
MAX_OPS = 512
MAX_DELTA = 100_000
STALE = 0.5
SENT, LATE, FAILED = "sent", "late", "failed"


@dataclass(eq=False)
class Op:
    kind: str  # move | buttons | abs | keys | release | action | sync
    dx: int = 0
    dy: int = 0
    wheel: int = 0
    buttons: int = 0
    x: float = 0.0
    y: float = 0.0
    mods: int = 0
    keys: list[int] = field(default_factory=list)
    urgent: bool = False
    id: Any = None
    name: str = ""
    run: Callable | None = None
    body: Any = None
    at: float = field(default_factory=time.monotonic)  # when it was queued

    def take(self) -> tuple[int, int, int]:
        """The next report's share of the pending movement (each value within +-127)."""
        n = max(1, math.ceil(max(abs(self.dx), abs(self.dy), abs(self.wheel)) / 127))
        cx, cy, cw = (int(v / n) if n > 1 else v for v in (self.dx, self.dy, self.wheel))
        self.dx, self.dy, self.wheel = self.dx - cx, self.dy - cy, self.wheel - cw
        return cx, cy, cw

    @property
    def empty(self) -> bool:
        return not (self.dx or self.dy or self.wheel)


def _int(msg: dict, key: str, default: int = 0, lo: int = -MAX_DELTA, hi: int = MAX_DELTA) -> int:
    v = msg.get(key, default)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ValueError(f"{key} must be a number")
    v = int(round(v))
    if not lo <= v <= hi:
        raise ValueError(f"{key} out of range [{lo}, {hi}]")
    return v


def _norm(msg: dict, key: str) -> float:
    v = msg.get(key)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ValueError(f"{key} must be a number (normalized 0..1)")
    return min(max(float(v), 0.0), 1.0)


class LiveSession:
    """One /control connection. `send` delivers a JSON-able dict to the client."""

    def __init__(self, device, send: Callable[[dict], Awaitable[None]], *, tick: float = TICK,
                 max_ops: int = MAX_OPS, stale: float = STALE):
        self.device = device
        self._send = send
        self.tick = tick
        self.max_ops = max_ops
        self.stale = stale
        self._ops: deque[Op] = deque()
        self._wake = asyncio.Event()
        self._space = asyncio.Event()
        self._space.set()
        self._executor = ThreadPoolExecutor(1, thread_name_prefix=f"live-{device.id}")
        self._queued_buttons = 0  # after every queued op has run
        self._sent_buttons = 0  # as last sent to the device
        self._sent_mods, self._sent_keys = 0, []  # keyboard state as last sent
        self._drops, self._drop_note_at = 0, -math.inf
        self._touched = False  # anything was sent to the device
        self._last_report_at = -math.inf
        self._tasks: list[asyncio.Task] = []
        self._last_error_at = -math.inf
        self.totals: Counter[str] = Counter()
        self._window: Counter[str] = Counter()
        self._report_time = 0.0

    # -- lifecycle ------------------------------------------------------------------------------

    def start(self) -> None:
        self._tasks = [asyncio.ensure_future(self._worker()), asyncio.ensure_future(self._stats())]

    async def close(self) -> None:
        """Stop, drop what was not sent yet, and release every key and button (always).

        The release is queued before the first await, so it happens even when this coroutine is
        cancelled (client gone, server shutting down); the executor runs it after any call still
        in flight."""
        for t in self._tasks:
            t.cancel()
        self._ops.clear()
        # a session that never sent anything cannot have left anything held
        fut = self._executor.submit(self.device.release_all) if self._touched else None
        self._executor.shutdown(wait=False)
        if fut is not None:
            try:
                await asyncio.wait_for(asyncio.shield(asyncio.wrap_future(fut)), 10.0)
            except Exception:  # TimeoutError, HidError: nothing more can be done from here
                pass
        await asyncio.gather(*self._tasks, return_exceptions=True)

    # -- input ----------------------------------------------------------------------------------

    async def handle(self, msg: dict) -> None:
        """Apply one client message; invalid ones are answered with an error, never raised."""
        t = msg.get("t")
        self._count("messages")
        try:
            if t == "mouse":
                dx, dy, wheel = _int(msg, "dx"), _int(msg, "dy"), _int(msg, "wheel")
                buttons = _int(msg, "buttons", self._queued_buttons, 0, 7)
                if dx or dy or wheel:
                    tail = self._ops[-1] if self._ops else None
                    if tail is not None and tail.kind == "move" and not self._stale(tail):
                        tail.dx, tail.dy, tail.wheel = tail.dx + dx, tail.dy + dy, tail.wheel + wheel
                        self._count("coalesced")
                    else:
                        await self._push(Op("move", dx=dx, dy=dy, wheel=wheel))
                if buttons != self._queued_buttons:
                    self._queued_buttons = buttons
                    await self._push(Op("buttons", buttons=buttons, urgent=True))
                self._wake.set()
            elif t == "abs":
                x, y = _norm(msg, "x"), _norm(msg, "y")
                buttons = _int(msg, "buttons", self._queued_buttons, 0, 7)
                wheel = _int(msg, "wheel", 0, -127, 127)
                tail = self._ops[-1] if self._ops else None
                # never into a button change: the press must land where it was made
                if (tail is not None and tail.kind == "abs" and not tail.urgent and buttons == self._queued_buttons
                        and abs(tail.wheel + wheel) <= 127 and not self._stale(tail)):
                    tail.x, tail.y, tail.wheel = x, y, tail.wheel + wheel
                    self._count("coalesced")
                    self._wake.set()
                else:
                    urgent = buttons != self._queued_buttons
                    self._queued_buttons = buttons
                    await self._push(Op("abs", x=x, y=y, buttons=buttons, wheel=wheel, urgent=urgent))
            elif t == "keys":
                keys = msg.get("keys", [])
                if not isinstance(keys, list) or len(keys) > 6:
                    raise ValueError("keys must be a list of at most 6 HID usages")
                usages = [_int({"k": k}, "k", 0, 0, 255) for k in keys]
                await self._push(Op("keys", mods=_int(msg, "mods", 0, 0, 255), keys=usages))
            elif t == "release":
                self._queued_buttons = 0
                await self._push(Op("release"))
            elif t == "sync":
                await self._push(Op("sync", id=msg.get("id")))
            elif t == "ping":
                await self._emit({"t": "pong", "id": msg.get("id"), "ts": msg.get("ts")})
            elif isinstance(t, str) and t in actions.ACTIONS:
                params = {k: v for k, v in msg.items() if k not in ("t", "id")}
                run, body = actions.parse(t, params)
                await self._push(Op("action", id=msg.get("id"), name=t, run=run, body=body))
            else:
                raise ValueError(f"unknown message type {t!r}")
        except ValueError as e:
            self._count("rejected")
            reply = {"t": "result", "id": msg["id"], "ok": False} if "id" in msg else {"t": "error"}
            await self._emit({**reply, "error": actions.describe(e)})

    async def _push(self, op: Op) -> None:
        while len(self._ops) >= self.max_ops:
            self._space.clear()
            await self._space.wait()
        self._ops.append(op)
        self._wake.set()

    def _popleft(self) -> Op:
        op = self._ops.popleft()
        if len(self._ops) < self.max_ops:
            self._space.set()
        return op

    # -- output ---------------------------------------------------------------------------------

    async def _worker(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            if not self._ops:
                self._wake.clear()
                await self._wake.wait()
                continue
            op = self._ops[0]
            if op.kind in ("move", "abs") and not op.urgent and len(self._ops) == 1:
                # nothing waits behind it: keep to one report per tick and let input accumulate
                delay = self._last_report_at + self.tick - loop.time()
                if delay > 0:
                    self._wake.clear()
                    try:
                        await asyncio.wait_for(self._wake.wait(), delay)
                    except asyncio.TimeoutError:  # not the builtin TimeoutError before Python 3.11
                        pass
                    continue
            if op.kind == "move":
                if self._stale(op):
                    self._popleft()
                    await self._dropped()
                    continue
                dx, dy, wheel = op.take()
                if op.empty:
                    self._popleft()
                self._last_report_at = loop.time()
                if await self._report(self.device.live_mouse, dx, dy, self._sent_buttons, wheel,
                                      deadline=op.at + self.stale) == LATE:
                    await self._dropped()
                continue
            self._popleft()
            if op.kind == "buttons":
                await self._buttons(op, lambda b: (self.device.live_mouse, 0, 0, b, 0))
            elif op.kind == "abs":
                self._last_report_at = loop.time()
                live_abs = getattr(self.device, "live_abs", None)
                if live_abs is None:
                    await self._error("this device has no absolute pointer")
                elif op.wheel:
                    await self._buttons(op, lambda b: (live_abs, op.x, op.y, b, op.wheel), motion=True)
                else:
                    await self._buttons(op, lambda b: (live_abs, op.x, op.y, b), motion=True)
            elif op.kind == "keys":
                await self._keys(op)
            elif op.kind == "release":
                self._sent_buttons, self._sent_mods, self._sent_keys = 0, 0, []
                await self._report(self.device.release_all)
            elif op.kind == "sync":
                await self._emit({"t": "result", "id": op.id, "ok": True, "result": {"reports": self.totals["reports"]}})
            elif op.kind == "action":
                await self._action(op)

    # Presses and motion may be dropped when late; the releases an op carries always go out.

    async def _buttons(self, op: Op, report: Callable[[int], tuple], *, motion: bool = False) -> None:
        """A report setting the buttons to op.buttons (`report(buttons)` -> (fn, *args))."""
        sent = self._sent_buttons
        keep = op.buttons & sent  # the new state without its presses
        if op.buttons == keep and (op.buttons != sent or not motion):
            outcome = await self._report(*report(op.buttons))  # only releases: never dropped
        else:
            outcome = LATE if self._stale(op) else await self._report(*report(op.buttons), deadline=op.at + self.stale)
            if outcome == LATE:
                await self._dropped()
                if keep == sent:
                    return
                op.buttons = keep
                outcome = await self._report(*report(keep))
        if outcome != LATE:
            self._sent_buttons = op.buttons

    async def _keys(self, op: Op) -> None:
        sent_mods, sent_keys = self._sent_mods, self._sent_keys
        keep_mods, keep_keys = op.mods & sent_mods, [k for k in sent_keys if k in op.keys]
        if op.mods == keep_mods and set(op.keys) <= set(keep_keys):
            outcome = await self._report(self.device.live_keys, op.mods, op.keys)  # only releases
        else:
            outcome = LATE if self._stale(op) else await self._report(self.device.live_keys, op.mods, op.keys,
                                                                      deadline=op.at + self.stale)
            if outcome == LATE:
                await self._dropped()
                if (keep_mods, keep_keys) == (sent_mods, sent_keys):
                    return
                op.mods, op.keys = keep_mods, keep_keys
                outcome = await self._report(self.device.live_keys, keep_mods, keep_keys)
        if outcome != LATE:
            self._sent_mods, self._sent_keys = op.mods, list(op.keys)

    def _stale(self, op: Op) -> bool:
        return time.monotonic() - op.at > self.stale

    async def _call(self, fn, *args):
        self._touched = True
        return await asyncio.get_running_loop().run_in_executor(self._executor, fn, *args)

    def _in_time(self, deadline: float, fn, *args) -> bool:
        """On the session's thread: fn(*args) if the device is free by `deadline` (a REST action
        holds its action lock meanwhile), else nothing is sent (False)."""
        lock = getattr(self.device, "_action", None)
        wait = deadline - time.monotonic()
        if lock is None:
            if wait < 0:
                return False
            fn(*args)
            return True
        if not (lock.acquire(timeout=wait) if wait > 0 else lock.acquire(blocking=False)):
            return False
        try:
            fn(*args)
        finally:
            lock.release()
        return True

    async def _report(self, fn, *args, deadline: float | None = None) -> str:
        """Send one report: SENT, LATE (not sent: the device was not free by `deadline`) or FAILED
        (the operator was told)."""
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        try:
            if deadline is None:
                await self._call(fn, *args)
            elif not await self._call(self._in_time, deadline, fn, *args):
                return LATE
        except Exception as e:  # HidError, PointerError...: tell the operator, keep going
            self._count("errors")
            await self._error(str(e) or type(e).__name__)
            return FAILED
        self._count("reports")
        self._report_time += loop.time() - t0
        return SENT

    async def _dropped(self) -> None:
        """Count a late op; tell the client at once, then at most once a second (the stats carry
        the counts)."""
        self._count("dropped")
        self._drops += 1
        now = time.monotonic()
        if now - self._drop_note_at < 1.0:
            return
        self._drop_note_at, n, self._drops = now, self._drops, 0
        busy = None
        with contextlib.suppress(Exception):
            busy = self.device.status().get("busy_with")
        await self._emit({"t": "dropped", "ops": n, "busy_with": busy,
                          "error": f"live input dropped: the phone was busy{f' ({busy})' if busy else ''} "
                                   f"for more than {self.stale:g} s"})

    async def _action(self, op: Op) -> None:
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        try:
            result = await self._call(op.run, self.device, op.body)
        except Exception as e:
            self._count("errors")
            reply = {"ok": False, "error": str(e) or type(e).__name__}
        else:
            reply = {"ok": True, "result": result}
        await self._emit({"t": "result", "id": op.id, "action": op.name,
                          "seconds": round(loop.time() - t0, 3), **reply})

    async def _error(self, message: str) -> None:
        """Report errors of live input at most twice a second (a locked phone fails every report)."""
        now = asyncio.get_running_loop().time()
        if now - self._last_error_at >= 0.5:
            self._last_error_at = now
            await self._emit({"t": "error", "error": message})

    async def _stats(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            w, self._window = self._window, Counter()
            report_ms = self._report_time / w["reports"] * 1000 if w["reports"] else None
            self._report_time = 0.0
            await self._emit({
                "t": "stats", "reports": w["reports"], "messages": w["messages"], "coalesced": w["coalesced"],
                "dropped": w["dropped"], "rejected": w["rejected"], "errors": w["errors"], "queue": len(self._ops),
                "report_ms": round(report_ms, 2) if report_ms is not None else None,
                "buttons": self._sent_buttons, "totals": dict(self.totals),
            })

    def _count(self, key: str) -> None:
        self.totals[key] += 1
        self._window[key] += 1

    async def _emit(self, msg: dict) -> None:
        try:
            await self._send(msg)
        except Exception:  # the socket is closing; close() will clean up
            pass


def dumps(msg: dict) -> str:
    return json.dumps(msg, default=str, separators=(",", ":"))
