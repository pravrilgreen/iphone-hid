"""Live video fan-out: one pump thread per watched device, any number of async viewers.

- The pump pulls frames from the device only while someone watches (the simulator renders on
  demand, so an unwatched phone costs nothing) and publishes the newest one; it never waits for
  viewers.
- A viewer only ever looks at the newest frame: a slow one skips frames instead of queueing them,
  so it cannot hold back the pump or other viewers.
- Full frames at full size are the capture card's own JPEG bytes (passthrough: no decode, no
  encode). Cropped or scaled variants are encoded once per frame and shared by every viewer that
  asked for the same variant; small thumbnails decode the JPEG at 1/2, 1/4 or 1/8 scale.
"""

from __future__ import annotations

import asyncio
import math
import threading
from concurrent.futures import Executor, Future
from contextlib import asynccontextmanager
from typing import AsyncIterator

import cv2
import numpy as np
from starlette.responses import JSONResponse, Response

from ..video.frame import Frame, encode_jpeg
from ..video.geometry import ScreenRect

_REDUCED = {2: cv2.IMREAD_REDUCED_COLOR_2, 4: cv2.IMREAD_REDUCED_COLOR_4, 8: cv2.IMREAD_REDUCED_COLOR_8}
_MAX_VARIANTS = 8
BOUNDARY = "frame"


def render_variant(frame: Frame, rect: ScreenRect | None, width: int | None, quality: int) -> bytes:
    """JPEG of the frame cropped to `rect` (None: whole frame) and scaled down to `width` pixels."""
    box = (rect or ScreenRect.from_pixels(0, 0, frame.width, frame.height)).clipped_to(frame.width, frame.height)
    c0, r0, c1, r1 = box.as_int_box()
    if c1 <= c0 or r1 <= r0:
        raise ValueError("the screen rectangle lies outside the frame")
    target = min(width or (c1 - c0), c1 - c0)  # never upscale
    factor = 1
    if frame.jpeg is not None and not frame.decoded:
        factor = next((f for f in (8, 4, 2) if (c1 - c0) / f >= target), 1)
    if factor == 1:
        img = frame.image
    else:
        img = cv2.imdecode(np.frombuffer(frame.jpeg, np.uint8), _REDUCED[factor])
        if img is None:
            raise ValueError("undecodable frame")
    img = img[round(r0 / factor):round(r1 / factor), round(c0 / factor):round(c1 / factor)]
    if target < c1 - c0:
        h = max(1, round((r1 - r0) * target / (c1 - c0)))  # aspect of the full-resolution box
        img = cv2.resize(img, (target, h), interpolation=cv2.INTER_AREA)
    return encode_jpeg(img, quality)


class TooManyViewers(Exception):
    pass


class Watcher:
    """One viewer's handle on a hub: waits for frames newer than the one it has."""

    def __init__(self, hub: FrameHub, loop: asyncio.AbstractEventLoop):
        self.hub = hub
        self.loop = loop
        self._event = asyncio.Event()

    def notify(self) -> None:  # from the pump thread
        try:
            self.loop.call_soon_threadsafe(self._event.set)
        except RuntimeError:  # loop closed: the viewer is gone
            pass

    async def next(self, prev: Frame | None, timeout: float, *, fail_fast: bool = False) -> Frame | None:
        """The newest frame if it is not `prev`, waiting up to `timeout` s for one; None on timeout
        (or, with `fail_fast`, as soon as the source reports an error before any frame came)."""
        deadline = self.loop.time() + timeout
        while True:
            self._event.clear()
            f = self.hub.latest
            if f is not None and f is not prev:
                return f
            if fail_fast and f is None and self.hub.error:
                return None
            remaining = deadline - self.loop.time()
            if remaining <= 0:
                return None
            try:
                await asyncio.wait_for(self._event.wait(), remaining)
            except asyncio.TimeoutError:  # not the builtin TimeoutError before Python 3.11
                return None


class FrameHub:
    def __init__(self, device, encoder: Executor, *, linger: float = 2.0, frame_timeout: float = 1.0):
        self.device = device
        self.encoder = encoder
        self.linger = linger
        self.frame_timeout = frame_timeout
        self.latest: Frame | None = None
        self.error: str | None = None
        self.fps = 0.0
        self._watchers: set[Watcher] = set()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._wanted = threading.Event()
        self._variants: dict[tuple, tuple[object, Future]] = {}

    @property
    def watchers(self) -> int:
        return len(self._watchers)

    @asynccontextmanager
    async def watch(self, limit: int | None = None) -> AsyncIterator[Watcher]:
        """Watch the device's frames; TooManyViewers when `limit` viewers already watch."""
        w = Watcher(self, asyncio.get_running_loop())
        with self._lock:
            if limit is not None and len(self._watchers) >= limit:
                raise TooManyViewers(f"{self.device.id} already has {len(self._watchers)} viewers (the most allowed)")
            self._watchers.add(w)
            self._wanted.set()
            if self._thread is None and not self._stop.is_set():
                self._thread = threading.Thread(target=self._pump, name=f"frames-{self.device.id}", daemon=True)
                self._thread.start()
        try:
            yield w
        finally:
            with self._lock:
                self._watchers.discard(w)
                if not self._watchers:  # the next viewer must never get a stale frame
                    self.latest, self.error = None, None

    def close(self) -> None:
        self._stop.set()
        self._wanted.set()
        t = self._thread
        if t is not None:
            t.join(timeout=3)

    def _pump(self) -> None:
        seq, last_t = -1, None
        while not self._stop.is_set():
            with self._lock:
                watchers = list(self._watchers)
                if not watchers:
                    self.latest, self.error, self.fps = None, None, 0.0  # never hand a stale frame out
                    self._wanted.clear()
            if not watchers:
                if not self._wanted.wait(self.linger):
                    with self._lock:
                        if not self._watchers:
                            self._thread = None
                            return
                continue
            try:
                f = self.device.frame(newer_than=seq, timeout=self.frame_timeout)
            except Exception as e:  # no video (TimeoutError, OSError...): keep trying while watched
                self.error = f"no video: {e}" if str(e) else "no video"
                self.fps = 0.0
                for w in watchers:
                    w.notify()
                self._stop.wait(0.2)
                continue
            self.error = None
            seq = f.seq
            if last_t is not None and f.ts > last_t:
                inst = 1.0 / (f.ts - last_t)
                self.fps = inst if not self.fps else 0.9 * self.fps + 0.1 * inst
            last_t = f.ts
            self.latest = f
            for w in watchers:
                w.notify()

    async def encoded(self, frame: Frame, *, crop: bool, width: int | None, quality: int) -> bytes:
        """The bytes to send for `frame`: its own JPEG when the viewer wants the full frame at full
        size, else a shared encoding of the variant."""
        if not crop and not width and frame.jpeg is not None:
            return frame.jpeg
        # identical pixels (a static screen re-sent under a new seq) share one encoding
        source = frame.jpeg if frame.jpeg is not None else frame
        key = (crop, width, quality)
        with self._lock:
            entry = self._variants.get(key)
            if entry is None or entry[0] is not source:
                rect = self.device.screen_rect() if crop else None
                fut = self.encoder.submit(render_variant, frame, rect, width, quality)
                self._variants.pop(key, None)
                self._variants[key] = (source, fut)
                while len(self._variants) > _MAX_VARIANTS:
                    self._variants.pop(next(iter(self._variants)))
            else:
                fut = entry[1]
        # shield: a viewer that goes away must not cancel an encoding other viewers wait for
        return await asyncio.shield(asyncio.wrap_future(fut))


class Hubs:
    """FrameHub per device id, created on first use."""

    def __init__(self, encoder: Executor):
        self.encoder = encoder
        self._hubs: dict[str, FrameHub] = {}
        self._lock = threading.Lock()

    def get(self, device) -> FrameHub:
        with self._lock:
            hub = self._hubs.get(device.id)
            if hub is None or hub.device is not device:
                hub = self._hubs[device.id] = FrameHub(device, self.encoder)
            return hub

    def close(self) -> None:
        with self._lock:
            hubs = list(self._hubs.values())
        for hub in hubs:
            hub.close()


class Pacer:
    """Frame pacing for one viewer: at most `fps` frames per second (0 = as they come), and a
    frame whose pixels did not change is re-sent at most once a second."""

    def __init__(self, fps: float = 0.0, keepalive: float = 1.0):
        self.fps = fps
        self.keepalive = keepalive
        self.last_sent_at = -math.inf
        self.last_frame: Frame | None = None
        self.last_jpeg: object = None

    def delay(self, now: float) -> float:
        return 0.0 if not self.fps else max(0.0, self.last_sent_at + 1.0 / self.fps - now)

    def wanted(self, frame: Frame, now: float) -> bool:
        same = frame is self.last_frame or (frame.jpeg is not None and frame.jpeg is self.last_jpeg)
        return not same or now - self.last_sent_at >= self.keepalive

    def sent(self, frame: Frame, now: float) -> None:
        self.last_sent_at = now
        self.last_frame = frame
        self.last_jpeg = frame.jpeg


def mjpeg_part(data: bytes, last: bool = False) -> bytes:
    """One multipart part, followed by the next boundary so browsers show it at once (or by the
    closing delimiter after the last part)."""
    head = f"Content-Type: image/jpeg\r\nContent-Length: {len(data)}\r\n\r\n".encode()
    return head + data + (f"\r\n--{BOUNDARY}--\r\n" if last else f"\r\n--{BOUNDARY}\r\n").encode()


class MJPEGResponse(Response):
    """multipart/x-mixed-replace stream of JPEG parts. Ends when the client goes away or after
    `frames` parts; answers 503 when the device has no video."""

    media_type = f"multipart/x-mixed-replace; boundary={BOUNDARY}"

    def __init__(self, hub: FrameHub, *, fps: float, crop: bool, width: int | None, quality: int,
                 frames: int | None, first_timeout: float = 3.0, max_viewers: int | None = None):
        self.status_code = 200
        self.background = None
        self.hub = hub
        self.fps = fps
        self.crop = crop
        self.width = width
        self.quality = quality
        self.frames = frames
        self.first_timeout = first_timeout
        self.max_viewers = max_viewers

    async def __call__(self, scope, receive, send) -> None:
        try:
            async with self.hub.watch(self.max_viewers) as w:
                await self._serve(w, scope, receive, send)
        except TooManyViewers as e:
            await JSONResponse({"error": str(e), "code": "too_many"}, status_code=429)(scope, receive, send)

    async def _serve(self, w: Watcher, scope, receive, send) -> None:
        first = await w.next(None, self.first_timeout, fail_fast=True)
        if first is None:
            msg = self.hub.error or "no video frame in time"
            await JSONResponse({"error": msg, "code": "no_video"}, status_code=503)(scope, receive, send)
            return
        await send({"type": "http.response.start", "status": 200, "headers": [
            (b"content-type", self.media_type.encode()),
            (b"cache-control", b"no-cache, no-store, must-revalidate"),
            (b"pragma", b"no-cache"),
            (b"x-accel-buffering", b"no"),
        ]})
        streamer = asyncio.ensure_future(self._stream(w, first, send))
        listener = asyncio.ensure_future(_wait_disconnect(receive))
        try:
            await asyncio.wait({streamer, listener}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in (streamer, listener):
                t.cancel()
            await asyncio.gather(streamer, listener, return_exceptions=True)
        if streamer.done() and not streamer.cancelled() and streamer.exception() is None:
            try:
                await send({"type": "http.response.body", "body": b"", "more_body": False})
            except OSError:
                pass

    async def _stream(self, w: Watcher, frame: Frame, send) -> None:
        loop = asyncio.get_running_loop()
        pacer = Pacer(self.fps)
        await send({"type": "http.response.body", "body": f"--{BOUNDARY}\r\n".encode(), "more_body": True})
        sent = 0
        while True:
            data = await self.hub.encoded(frame, crop=self.crop, width=self.width, quality=self.quality)
            sent += 1
            last = bool(self.frames) and sent >= self.frames
            await send({"type": "http.response.body", "body": mjpeg_part(data, last), "more_body": True})
            pacer.sent(frame, loop.time())
            if last:
                return
            while True:
                delay = pacer.delay(loop.time())
                if delay:
                    await asyncio.sleep(delay)
                nxt = await w.next(frame, 1.0)
                if nxt is not None:
                    frame = nxt
                elif self.hub.error:
                    continue  # no video: do not keep re-sending the last picture
                if pacer.wanted(frame, loop.time()):
                    break


async def _wait_disconnect(receive) -> None:
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            return
