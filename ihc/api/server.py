"""HTTP/WebSocket API and web console for the phones of one host.

    app = create_app(registry, public_url="http://192.168.1.20:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000, ws_per_message_deflate=False)

Concurrency model:
- the event loop never blocks: device actions run on one worker thread per device (so the requests
  of one phone run in arrival order and never hold up another phone), screenshots and status
  checks on an I/O pool, JPEG variants on an encoder pool;
- video comes from one pump thread per watched device (ihc.api.stream), shared by every viewer;
- each /control connection owns a worker thread that sends its HID reports in order
  (ihc.api.control), and releases everything when the connection ends; one per device at a time.

Access control (token, Host, Origin, request shape) is one middleware in front of everything
(ihc.api.security). A device job whose HTTP client left before it started is skipped, and a
repeated Idempotency-Key gets the first request's outcome instead of acting again.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import math
import os
import threading
import time
from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Iterable, Literal, Optional

from fastapi import Body, Depends, FastAPI, Header, Path as PathParam, Query, Request, WebSocket
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocketDisconnect

from ..hid.base import HidError, HidTimeout
from ..input.pointer import PointerError
from . import actions, models
from .control import LiveSession, dumps
from .security import MAX_BODY, CalibrationKeys, Guard, Policy
from .stream import BOUNDARY, Hubs, MJPEGResponse, Pacer, TooManyViewers

VERSION = "0.1.0"
WEB_DIR = Path(__file__).resolve().parents[2] / "web"
STATUS_INTERVAL = 0.5
ACK_TIMEOUT = 2.0
MAX_VIEWERS = 16  # video viewers per device (/stream sockets and MJPEG streams)
MAX_WEBSOCKETS = 256  # /stream and /control sockets of the whole server
IDEMPOTENCY_TTL = 600.0  # a key's outcome is replayed this long after its job ended
IDEMPOTENCY_MAX = 1024
DISCONNECT_POLL = 0.1  # how often a queued job checks that its client is still there
NO_CACHE = {"Cache-Control": "no-cache, no-store, must-revalidate"}
SimOp = Literal["lock", "unlock", "prompt_accessory", "allow_accessory", "signal_off", "signal_on", "home", "open_app"]
DeviceId = PathParam(description="Device id, as listed by GET /api/devices", examples=["sim-01"])

DESCRIPTION = """
Control iPhones through external hardware only: the screen comes from an HDMI capture card, input goes
through a HID chip (CH9329, or an ESP32 BLE bridge with the same protocol) acting as mouse and keyboard
for the iOS AssistiveTouch pointer.

**Coordinates** are normalized by default: (0, 0) is the top-left and (1, 1) the bottom-right corner of the
phone screen. `space: "pt"` takes iOS points, `space: "frame"` pixels of the captured video frame.

**Ordering**: the actions of one device run one at a time, in arrival order; an action returns once the phone
has received every report of it. Different devices never wait for each other.

**Errors** are always `{"error": "...", "code": "..."}`: 404 `not_found` (unknown device), 422
`invalid_input` (nothing was sent to the phone), 409 `device_error` (HID error, phone locked or accessory not
allowed, pointer or calibration failure), 503 `no_video`, 504 `hid_timeout`.

**Access**: when the server has a token (`ihc serve --token-file`, a box keeps it in `/var/lib/ihc/token`),
every `/api` route except `GET /api/health` needs `Authorization: Bearer <token>` (401 otherwise); WebSockets
and the MJPEG/screenshot URLs also take `?token=`. POST requests must be sent as `Content-Type:
application/json`, even with an empty body (415), at most 1 MB (413). Browsers from another origin
(WebSockets, POSTs) are refused (403), and so is a Host header that is not an IP address, a `.local` name or
this machine's name (400).

**Retries**: a request whose client went away before its action started is dropped (the phone does nothing).
An `Idempotency-Key` header makes a POST action safe to retry: a repeated key (per device, for 10 minutes)
returns the first request's outcome, waiting for it if it is still running, instead of acting again.

### Video
* `GET /api/devices/{id}/mjpeg`: `multipart/x-mixed-replace` JPEG stream (works in an `<img>`, VLC, OpenCV).
  Without `crop`/`width` the capture card's own JPEG bytes are forwarded untouched (no decode, no encode).
* **WebSocket** `/api/devices/{id}/stream` (query or JSON message: `fps`, `crop`, `width`, `quality`, `ack`):
  binary messages are JPEG frames (same passthrough rule), text messages
  `{"type": "status", ...device status, "frame": {"seq", "ts", "width", "height", "age_ms"}, "screen_rect", "stream"}`
  about twice a second. A slow client skips frames, it is never queued for. With `ack: true` the client answers
  each frame with `{"t": "ack"}` and never has more than one frame in flight.

### Live control (KVM)
**WebSocket** `/api/devices/{id}/control`, JSON messages:
* `{"t": "mouse", "dx", "dy", "buttons", "wheel"}`: relative input (e.g. Pointer Lock movementX/Y). Moves are
  coalesced to at most one HID report per ~16 ms tick and split when beyond ±127; a button change goes out at
  once, after the pending movement, so clicks are never lost or reordered. `buttons`: 1 left, 2 right (Home),
  4 middle (App Switcher).
* `{"t": "abs", "x", "y", "buttons", "wheel"}`: absolute position (0..1), for phones in absolute pointer mode.
* `{"t": "keys", "mods", "keys"}`: full keyboard state (modifier bits, up to 6 HID usages), sent at once.
* `{"t": "release"}`: release every key and button. Closing the socket always does this too.
* Actions: `{"t": "tap", "id": 1, "x": 0.5, "y": 0.5}` (same shapes as the POST endpoints), answered with
  `{"t": "result", "id", "ok", "result" | "error"}` in order with the live input; `{"t": "sync", "id"}` is answered
  once everything before it was sent.
* The server sends `{"t": "stats", "reports", "messages", "coalesced", "dropped", "errors", "queue", ...}` every second.
* Input that waited more than 0.5 s for the phone (a REST action or a calibration held it) is dropped instead of
  being replayed late: moves, wheel, button and key presses; releases always go out. The client gets
  `{"t": "dropped", "ops", "busy_with", "error"}`.
* One control connection per device: another one is closed with code 4409 unless it connects with
  `?takeover=true`, which closes the current one (code 4409, everything released) and takes its place.
* Close codes: 4401 token missing or wrong, 4404 unknown device, 4409 control in use or taken over, 4429 too
  many connections or viewers.
"""

TAGS = [
    {"name": "devices", "description": "Devices and their status"},
    {"name": "video", "description": "Screenshots and live video"},
    {"name": "actions", "description": "Input actions; each returns once the phone has received it"},
    {"name": "calibration", "description": "Pointer calibration through a web page opened in Safari on the phone"},
    {"name": "simulator", "description": "Operator controls of simulated phones (404 on hardware)"},
]


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def error_status(exc: BaseException) -> int:
    """HTTP status for an exception raised by a device call."""
    if isinstance(exc, ApiError):
        return exc.status
    if isinstance(exc, ValueError):  # includes pydantic's ValidationError
        return 422
    if isinstance(exc, HidTimeout):
        return 504
    if isinstance(exc, OSError):  # TimeoutError from the video source, capture errors
        return 503
    if isinstance(exc, (HidError, PointerError, RuntimeError)):  # CalibrationError is a RuntimeError
        return 409
    return 500


def error_message(exc: BaseException) -> str:
    text = actions.describe(exc) if isinstance(exc, ValueError) else str(exc)
    if isinstance(exc, OSError) and not isinstance(exc, HidError):
        text = f"no video: {text}" if text else "no video"
    return text or type(exc).__name__


def error_json(status: int, message: str, headers=None) -> JSONResponse:
    return JSONResponse({"error": message, "code": models.CODES.get(status, "internal")}, status_code=status,
                        headers=headers)


class _NoCacheStatic(StaticFiles):
    def file_response(self, *args, **kwargs) -> Response:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


class StreamSettings:
    """Per-viewer options of /stream, from the query string and later JSON messages."""

    def __init__(self) -> None:
        self.fps = 0.0  # 0: every frame
        self.crop = False
        self.width: int | None = None
        self.quality = 80
        self.ack = False

    def update(self, data: dict) -> None:
        def number(key, lo, hi):
            v = data[key]
            if isinstance(v, str):
                v = float(v)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not lo <= v <= hi:
                raise ValueError(f"{key} must be a number in [{lo}, {hi}]")
            return v

        def flag(key):
            v = data[key]
            return v.lower() in ("1", "true", "yes", "on") if isinstance(v, str) else bool(v)

        if "fps" in data:
            self.fps = float(number("fps", 0, 120))
        if "crop" in data:
            self.crop = flag("crop")
        if "width" in data:
            self.width = None if data["width"] in (None, "", 0, "0") else int(number("width", 16, 4096))
        if "quality" in data:
            self.quality = int(number("quality", 10, 95))
        if "ack" in data:
            self.ack = flag("ack")

    @property
    def passthrough(self) -> bool:
        return not self.crop and not self.width


class ClientGone(ApiError):
    """Every client waiting for a job left before it started: it was not run."""

    def __init__(self) -> None:
        super().__init__(499, "the client went away before the action started: it was not performed")


class _Job:
    """A device job shared by the requests carrying the same Idempotency-Key."""

    def __init__(self, fingerprint: tuple, request: Request):
        self.fingerprint = fingerprint
        self.requests = [request]
        self.outcome: asyncio.Future = asyncio.get_running_loop().create_future()  # (ok, result | ApiError)
        self.ended_at: float | None = None

    def end(self, ok: bool, value: Any) -> None:
        self.ended_at = time.monotonic()
        self.requests = []
        self.outcome.set_result((ok, value))


class _Control:
    """The /control connection of a device (at most one)."""

    def __init__(self, peer: str):
        self.peer = peer
        self.kicked = asyncio.Event()  # another client takes over
        self.closing = False  # its client is gone: the next one waits for the release, not refused
        self.done = asyncio.Event()  # the session has released everything


class _Farm:
    """Server state: the registry plus the executors and video hubs built on top of it."""

    def __init__(self, registry, public_url: str | None, log, *, max_viewers: int = MAX_VIEWERS,
                 max_websockets: int = MAX_WEBSOCKETS):
        self.registry = registry
        self.public_url = public_url.rstrip("/") if public_url else None
        self.log = log or (lambda event, **fields: None)
        self.started = time.time()
        cpus = os.cpu_count() or 2
        self.io = ThreadPoolExecutor(max(8, 2 * cpus), thread_name_prefix="ihc-io")
        self.encoder = ThreadPoolExecutor(max(2, cpus), thread_name_prefix="ihc-jpeg")
        self.hubs = Hubs(self.encoder)
        self.keys = CalibrationKeys()
        self.max_viewers = max_viewers
        self.max_websockets = max_websockets
        self.sockets = 0  # open /stream and /control sockets
        self.controls: dict[str, _Control] = {}
        self._jobs: OrderedDict[tuple[str, str], _Job] = OrderedDict()
        self._workers: dict[str, ThreadPoolExecutor] = {}
        self._lock = threading.Lock()

    def device(self, device_id: str):
        try:
            return self.registry.get(device_id)
        except KeyError:
            raise ApiError(404, f"no device {device_id!r}") from None

    def worker(self, device) -> ThreadPoolExecutor:
        with self._lock:
            ex = self._workers.get(device.id)
            if ex is None:
                ex = self._workers[device.id] = ThreadPoolExecutor(1, thread_name_prefix=f"ihc-{device.id}")
            return ex

    async def run(self, request: Request, device, name: str, fn, *args) -> Any:
        """Run a blocking device job for an HTTP request, on the device's own worker (in arrival
        order). Skipped when its client left before it started; once per Idempotency-Key."""
        key = request.headers.get("idempotency-key")
        if key is None:
            return await self._run([request], device, name, fn, *args)
        if not (0 < len(key) <= 200 and key.isascii() and key.isprintable()):
            raise ApiError(422, "Idempotency-Key must be 1 to 200 printable ASCII characters")
        fingerprint = (request.url.path, hashlib.sha256(await request.body()).hexdigest())
        slot = (device.id, key)
        job = self._jobs.get(slot)
        if job is not None and job.ended_at is not None and time.monotonic() - job.ended_at > IDEMPOTENCY_TTL:
            del self._jobs[slot]
            job = None
        if job is not None:
            if job.fingerprint != fingerprint:
                raise ApiError(422, f"Idempotency-Key {key!r} was already used for a different request")
            self._jobs.move_to_end(slot)
            job.requests.append(request)
            self.log("action_replayed", device=device.id, action=name, key=key, running=job.ended_at is None)
            ok, value = await asyncio.shield(job.outcome)
            if ok:
                return value
            raise value
        job = self._jobs[slot] = _Job(fingerprint, request)
        while len(self._jobs) > IDEMPOTENCY_MAX:
            self._jobs.popitem(last=False)
        try:
            value = await self._run(job.requests, device, name, fn, *args)
        except ApiError as e:
            if isinstance(e, ClientGone):
                self._jobs.pop(slot, None)  # never ran: a retry must run it
            job.end(False, e)
            raise
        except BaseException:  # cancelled (server shutting down): the outcome is unknown
            self._jobs.pop(slot, None)
            job.end(False, ApiError(503, "the server stopped while the action was pending"))
            raise
        job.end(True, value)
        return value

    async def _run(self, requests: list[Request], device, name: str, fn, *args) -> Any:
        fut = self.worker(device).submit(fn, *args)
        waiter = asyncio.wrap_future(fut)
        t0 = time.monotonic()
        while not fut.running() and not fut.done():
            await asyncio.wait({waiter}, timeout=DISCONNECT_POLL)
            if fut.running() or fut.done():
                break
            gone = True
            for r in requests:
                if not await r.is_disconnected():
                    gone = False
                    break
            if gone and fut.cancel():  # atomic: false once the worker picked the job up
                self.log("action_skipped_client_gone", device=device.id, action=name,
                         waited_s=round(time.monotonic() - t0, 3))
                raise ClientGone()
        try:
            return await waiter
        except ApiError:
            raise
        except Exception as e:
            raise ApiError(error_status(e), error_message(e)) from e

    async def on_io(self, fn, *args) -> Any:
        try:
            return await asyncio.get_running_loop().run_in_executor(self.io, fn, *args)
        except ApiError:
            raise
        except Exception as e:
            raise ApiError(error_status(e), error_message(e)) from e

    def base_url(self, request: Request) -> str:
        return self.public_url or str(request.base_url).rstrip("/")

    def page_url(self, request: Request, device, page_url: str | None = None) -> str:
        """The calibration page of `device` as the phone reaches it, with its current key."""
        return self.keys.url(device.id, page_url or f"{self.base_url(request)}/calibrate/{device.id}")

    def close(self) -> None:
        self.hubs.close()
        for ex in [*self._workers.values(), self.io, self.encoder]:
            ex.shutdown(wait=False, cancel_futures=True)


def _ok(result: Any = None) -> dict:
    return {"ok": True, "result": result}


def _check_event(ev: models.CalibrationEvent) -> dict:
    need = ("screen_w", "screen_h", "inner_w", "inner_h") if ev.type == "hello" else ("x", "y")
    for key in need:
        if getattr(ev, key) is None:
            raise ApiError(422, f"a {ev.type} event needs a number {key!r}")
    return ev.model_dump(exclude_none=True)


IdempotencyKey = Header(None, alias="Idempotency-Key", max_length=200,
                        description="Retry safely: a repeated key (per device, 10 minutes) returns the first "
                                    "request's outcome instead of acting again")
EXPIRED_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>iphone-hid calibration</title></head>
<body style="font: 17px -apple-system, system-ui, sans-serif; padding: 24px">
<h1>This calibration link has expired</h1>
<p>Each calibration link works for one calibration. Open the current link again: the console shows it, and so
does <code>GET /api/devices/&lt;id&gt;/calibration</code> (<code>page_url</code>).</p></body></html>"""


def create_app(registry, *, public_url: str | None = None, log=None, web_dir: str | Path | None = None,
               token: str | None = None, allowed_hosts: Iterable[str] = (), allow_origins: Iterable[str] = (),
               max_body: int = MAX_BODY, max_viewers: int = MAX_VIEWERS,
               max_websockets: int = MAX_WEBSOCKETS) -> FastAPI:
    """The API and web console for the devices of `registry` (which the caller keeps and closes).

    `public_url` is how phones reach this server (the calibration page is opened from it); by
    default the URL of each request is used. `log` is an event log callable (ihc.jsonlog.EventLog).
    `token`: the API token (None: anyone who reaches the server controls the phones);
    `allowed_hosts`: Host names accepted besides IP addresses, localhost, *.local, this machine's
    name and public_url's host ("*": any); `allow_origins`: browser origins accepted besides this
    server itself and public_url ("*": any). See ihc.api.security."""
    farm = _Farm(registry, public_url, log, max_viewers=max_viewers, max_websockets=max_websockets)
    web = Path(web_dir) if web_dir else WEB_DIR

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if not token:
            farm.log("auth_disabled", warning="no API token: anyone on this network can control the phones")
        try:
            yield
        finally:
            farm.close()

    app = FastAPI(title="iphone-hid", version=VERSION, description=DESCRIPTION, openapi_tags=TAGS, lifespan=lifespan)
    app.state.farm = farm
    app.add_middleware(Guard, policy=Policy(token=token, public_url=public_url, allowed_hosts=allowed_hosts,
                                            allow_origins=allow_origins, max_body=max_body), log=farm.log)

    # -- errors ---------------------------------------------------------------------------------

    @app.exception_handler(ApiError)
    async def _api_error(request, exc: ApiError):
        return error_json(exc.status, exc.message)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request, exc: RequestValidationError):
        return error_json(422, "; ".join(actions.describe_error(e) for e in exc.errors()) or "invalid request")

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request, exc: StarletteHTTPException):
        return error_json(exc.status_code, str(exc.detail), getattr(exc, "headers", None))

    @app.exception_handler(Exception)
    async def _internal_error(request, exc: Exception):
        farm.log("api_error", path=request.url.path, error=repr(exc))
        return error_json(500, f"internal error: {exc}")

    # -- devices --------------------------------------------------------------------------------

    @app.get("/api/health", tags=["devices"], summary="Server health", response_model=models.Health)
    async def health():
        """Liveness and the number of devices; cheap enough for load balancers and monitors."""
        return {"ok": True, "devices": len(registry.devices()), "uptime_s": round(time.time() - farm.started, 1),
                "public_url": farm.public_url, "version": VERSION}

    @app.get("/api/devices", tags=["devices"], summary="List devices", response_model=models.DeviceList)
    async def devices():
        """Status of every device of this host (never waits for running actions)."""
        return {"devices": [d.status() for d in registry.devices()]}

    @app.get("/api/devices/{device_id}", tags=["devices"], summary="Device status", response_model=models.DeviceStatus,
             responses={404: models.ERRORS[404]})
    async def device_status(device_id: str = DeviceId):
        """State (ready, busy, hid_disconnected, hid_offline, no_signal, needs_calibration), health, pointer,
        calibration, HID counters and the last action's result."""
        return farm.device(device_id).status()

    # -- video ----------------------------------------------------------------------------------

    @app.get("/api/devices/{device_id}/screenshot", tags=["video"], summary="Screenshot", response_class=Response,
             responses={200: {"content": {"image/jpeg": {}, "image/png": {}}, "description": "The image"},
                        404: models.ERRORS[404], 503: {"model": models.ErrorResponse, "description": "No video"}})
    async def screenshot(device_id: str = DeviceId,
                         format: Literal["jpeg", "jpg", "png"] = Query("jpeg", description="Image format"),
                         crop: bool = Query(True, description="Crop to the phone screen (else the whole frame)"),
                         quality: int = Query(85, ge=10, le=100, description="JPEG quality")):
        """The current screen. An uncropped JPEG is the capture card's own frame."""
        device = farm.device(device_id)
        fmt = "png" if format == "png" else "jpeg"
        data = await farm.on_io(lambda: device.screenshot(fmt=fmt, crop=crop, quality=quality))
        return Response(data, media_type=f"image/{fmt}", headers={"Cache-Control": "no-store"})

    @app.get("/api/devices/{device_id}/mjpeg", tags=["video"], summary="MJPEG live stream", response_class=Response,
             responses={200: {"content": {f"multipart/x-mixed-replace; boundary={BOUNDARY}": {}},
                              "description": "Endless stream of JPEG parts"},
                        404: models.ERRORS[404], 503: {"model": models.ErrorResponse, "description": "No video"}})
    async def mjpeg(device_id: str = DeviceId,
                    fps: float = Query(0.0, ge=0, le=120, description="Frame rate cap (0: every captured frame)"),
                    crop: bool = Query(False, description="Crop to the phone screen (re-encodes)"),
                    width: Optional[int] = Query(None, ge=16, le=4096, description="Scale down to this width (re-encodes)"),
                    quality: int = Query(80, ge=10, le=95, description="JPEG quality of re-encoded frames"),
                    frames: Optional[int] = Query(None, ge=1, description="End the stream after this many frames")):
        """Without `crop` and `width` every part is the capture card's own JPEG, forwarded untouched. Cropped
        or scaled variants are encoded once per frame and shared by all viewers. A slow client skips frames;
        a frame whose pixels did not change is re-sent at most once a second."""
        device = farm.device(device_id)
        return MJPEGResponse(farm.hubs.get(device), fps=fps, crop=crop, width=width, quality=quality, frames=frames,
                             max_viewers=farm.max_viewers)

    @app.websocket("/api/devices/{device_id}/stream")
    async def stream(ws: WebSocket, device_id: str):
        await ws.accept()
        device = await _ws_device(ws, device_id)
        if device is None or not await _admit(ws):
            return
        try:
            await _stream(ws, device)
        finally:
            farm.sockets -= 1

    async def _stream(ws: WebSocket, device) -> None:
        settings = StreamSettings()
        try:
            settings.update(dict(ws.query_params))
        except (ValueError, TypeError) as e:
            await ws.send_text(dumps({"type": "error", "error": str(e)}))
        hub = farm.hubs.get(device)
        acked = asyncio.Event()
        acked.set()
        send_lock = asyncio.Lock()

        async def send_text(msg: dict) -> None:
            async with send_lock:
                await ws.send_text(dumps(msg))

        async def receiver() -> None:
            while True:
                msg = await ws.receive()
                if msg["type"] == "websocket.disconnect":
                    return
                try:
                    data = json.loads(msg.get("text") or "null")
                except ValueError:
                    data = None
                if not isinstance(data, dict):
                    continue
                if data.get("t") == "ack":
                    acked.set()
                    continue
                try:
                    settings.update(data)
                except (ValueError, TypeError) as e:
                    await send_text({"type": "error", "error": str(e)})
                if not settings.ack:
                    acked.set()

        async def sender(w) -> None:
            loop = asyncio.get_running_loop()
            pacer = Pacer(settings.fps)
            seen = last = None
            last_age_ms = None
            sent, sent_at = 0, deque(maxlen=240)
            next_status = 0.0
            while True:
                now = loop.time()
                if now >= next_status:
                    next_status = now + STATUS_INTERVAL
                    recent = sum(1 for t in sent_at if now - t <= 2.0)
                    await send_text({
                        "type": "status", **device.status(),
                        "frame": None if last is None else {"seq": last.seq, "ts": last.ts, "width": last.width,
                                                            "height": last.height, "age_ms": last_age_ms},
                        "screen_rect": device.screen_rect().to_dict(),
                        "stream": {"fps": round(recent / 2.0, 1), "source_fps": round(hub.fps, 1), "sent": sent,
                                   "passthrough": settings.passthrough and (last is None or last.jpeg is not None),
                                   "crop": settings.crop, "width": settings.width, "ack": settings.ack,
                                   "viewers": hub.watchers, "error": hub.error},
                    })
                    continue
                budget = next_status - now
                if settings.ack and not acked.is_set():
                    if now - pacer.last_sent_at < ACK_TIMEOUT:
                        try:
                            await asyncio.wait_for(acked.wait(), budget)
                        except asyncio.TimeoutError:  # not the builtin TimeoutError before Python 3.11
                            pass
                        continue
                    acked.set()  # the client stopped answering: do not stall forever
                pacer.fps = settings.fps
                delay = pacer.delay(now)
                if delay:
                    await asyncio.sleep(min(delay, budget))
                    continue
                nxt = await w.next(seen, budget)
                if nxt is not None:
                    seen = nxt
                elif hub.error:
                    continue  # no video: the status says so; do not keep re-sending the last picture
                if seen is None or not pacer.wanted(seen, loop.time()):
                    continue
                data = await hub.encoded(seen, crop=settings.crop, width=settings.width, quality=settings.quality)
                if settings.ack:
                    acked.clear()
                async with send_lock:
                    await ws.send_bytes(data)
                now = loop.time()
                pacer.sent(seen, now)
                last, last_age_ms = seen, round((time.monotonic() - seen.ts) * 1000, 1)
                sent += 1
                sent_at.append(now)

        try:
            async with hub.watch(farm.max_viewers) as w:
                farm.log("ws_open", kind="stream", device=device.id)
                await _run_until_first_exits(receiver(), sender(w))
        except TooManyViewers as e:
            await send_text({"type": "error", "error": str(e), "code": "too_many"})
            await ws.close(code=4429, reason="too many viewers")
            return
        farm.log("ws_close", kind="stream", device=device.id)

    # -- actions --------------------------------------------------------------------------------

    def add_action(name: str, spec: actions.Spec) -> None:
        optional = all(not f.is_required() for f in spec.model.model_fields.values())

        async def endpoint(request: Request, device_id: str, body: Any = None, idempotency_key: Any = None):
            device = farm.device(device_id)
            body = body if body is not None else spec.model()
            return _ok(await farm.run(request, device, name, spec.run, device, body))

        # FastAPI reads the signature: give it the real body model, so it validates and documents it
        endpoint.__signature__ = inspect.Signature([
            inspect.Parameter("request", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=Request),
            inspect.Parameter("device_id", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=str, default=DeviceId),
            inspect.Parameter("body", inspect.Parameter.POSITIONAL_OR_KEYWORD,
                              annotation=Optional[spec.model] if optional else spec.model,
                              default=Body(None) if optional else Body(...)),
            inspect.Parameter("idempotency_key", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=Optional[str],
                              default=IdempotencyKey),
        ])
        endpoint.__name__ = f"action_{name}"
        app.add_api_route(f"/api/devices/{{device_id}}/{name}", endpoint, methods=["POST"], tags=["actions"],
                          name=f"action_{name}", summary=spec.summary, description=spec.description,
                          response_model=models.ActionResponse, responses=models.ERRORS)

    for _name, _spec in actions.ACTIONS.items():
        add_action(_name, _spec)

    @app.post("/api/devices/{device_id}/actions", tags=["actions"], summary="Run a script of actions",
              response_model=models.ScriptResponse, responses={404: models.ERRORS[404], 422: models.ERRORS[422]})
    async def run_script(request: Request, body: models.ScriptRequest, device_id: str = DeviceId,
                         idempotency_key: Optional[str] = IdempotencyKey):
        """Steps run in order on the device, as one job (other requests for the device wait until it ends).
        Every step is validated before the first one runs (422 names the bad step). A failing step does not make
        the request fail: the response has `ok: false`, the first `error`, and every step's result."""
        device = farm.device(device_id)
        steps = []
        for i, step in enumerate(body.actions):
            params = {k: v for k, v in step.model_dump().items() if k != "type"}
            try:
                run, parsed = actions.parse(step.type, params, script=True)
            except ValueError as e:
                raise ApiError(422, f"actions[{i}] ({step.type}): {actions.describe(e)}") from None
            steps.append((step.type, run, parsed))

        def run_all() -> dict:
            results, t0 = [], time.monotonic()
            for i, (kind, run, parsed) in enumerate(steps):
                s0 = time.monotonic()
                try:
                    results.append({"index": i, "type": kind, "ok": True, "result": run(device, parsed),
                                    "seconds": round(time.monotonic() - s0, 3)})
                except Exception as e:
                    results.append({"index": i, "type": kind, "ok": False, "error": error_message(e),
                                    "status": error_status(e), "seconds": round(time.monotonic() - s0, 3)})
                    if body.stop_on_error:
                        break
            return {"steps": results, "completed": sum(r["ok"] for r in results), "total": len(steps),
                    "seconds": round(time.monotonic() - t0, 3)}

        out = await farm.run(request, device, "actions", run_all)
        failed = [r for r in out["steps"] if not r["ok"]]
        if failed:
            f = failed[0]
            return {"ok": False, "error": f"step {f['index']} ({f['type']}): {f['error']}", "result": out}
        return {"ok": True, "result": out}

    # -- calibration ----------------------------------------------------------------------------

    @app.get("/api/devices/{device_id}/calibration", tags=["calibration"], summary="Calibration summary",
             response_model=models.CalibrationSummary, responses={404: models.ERRORS[404]})
    async def calibration(request: Request, device_id: str = DeviceId):
        """How the pointer is calibrated: method ("guess" = not yet, "safari", "sim"), when, the landing error
        of the validation moves, the pointer mode, and the calibration page's current URL (to open it in Safari
        by hand before a calibration with open_page=false)."""
        device = farm.device(device_id)
        st = device.status()
        return {**st["calibration"], "mode": st["pointer"].get("mode", "relative"),
                "page_url": farm.page_url(request, device)}

    @app.post("/api/devices/{device_id}/calibrate", tags=["calibration"], summary="Calibrate the pointer",
              response_model=models.ActionResponse, responses=models.ERRORS)
    async def calibrate(request: Request, body: Optional[actions.Calibrate] = Body(None), device_id: str = DeviceId,
                        idempotency_key: Optional[str] = IdempotencyKey):
        """Opens the calibration page in Safari through Spotlight, then measures the pointer from the clicks the
        page reports (about a minute; do not touch the phone meanwhile). The phone must reach `page_url`: by
        default `<public_url>/calibrate/<id>`, so it must be on the same network as this host. The page's URL
        carries a key (`k`) that its events must present; a new one is made when the calibration ends."""
        device = farm.device(device_id)
        body = body or actions.Calibrate()
        options = body.options.kwargs()

        def job() -> dict:
            page_url = farm.page_url(request, device, body.page_url)  # the key current when the job starts
            rejected = farm.keys.rejected[device.id]
            farm.log("calibrate_start", device=device.id, page_url=page_url.partition("?")[0], open_page=body.open_page)
            try:
                result = device.calibrate(page_url=page_url if body.open_page else None, **options)
            except Exception as e:
                if farm.keys.rejected[device.id] > rejected and error_status(e) == 409:
                    raise ApiError(409, f"{error_message(e)} (the page on the phone sent events with an expired key: "
                                        "open its current link, GET .../calibration page_url, and try again)") from e
                raise
            finally:
                farm.keys.rotate(device.id)
            return {**result, "page_url": page_url}

        return _ok(await farm.run(request, device, "calibrate", job))

    def calibration_key(device_id: str = DeviceId,
                        k: str = Query("", max_length=100, description="The page's calibration key (from its URL)")):
        """The device, once the calibration page's key is checked (before the body is)."""
        device = farm.device(device_id)
        if not farm.keys.check(device.id, k):
            raise ApiError(403, "wrong or expired calibration key: open the calibration page from its current link")
        return device

    @app.post("/api/devices/{device_id}/calibration/events", tags=["calibration"], summary="Calibration page events",
              response_model=models.ActionResponse,
              responses={403: {"model": models.ErrorResponse, "description": "Wrong or expired calibration key"},
                         404: models.ERRORS[404], 422: models.ERRORS[422]})
    async def calibration_events(body: models.CalibrationEvents, device=Depends(calibration_key)):
        """Posted by web/calibrate.html running in Safari on the phone: one event or a list, applied in order.
        Needs no API token but the page's key `k` (query parameter), which the page has in its own URL."""
        events = [_check_event(ev) for ev in (body if isinstance(body, list) else [body])]
        for ev in events:
            device.clicks.push(ev)
        return _ok({"accepted": len(events)})

    @app.get("/calibrate/{device_id}", include_in_schema=False)
    async def calibration_page(device_id: str, k: str = Query("", max_length=100)):
        device = farm.device(device_id)
        if not farm.keys.check(device.id, k):
            return HTMLResponse(EXPIRED_PAGE, status_code=403, headers=NO_CACHE)
        return FileResponse(web / "calibrate.html", media_type="text/html", headers=NO_CACHE)

    # -- live control ---------------------------------------------------------------------------

    @app.websocket("/api/devices/{device_id}/control")
    async def control(ws: WebSocket, device_id: str):
        await ws.accept()
        device = await _ws_device(ws, device_id, key="t")
        if device is None or not await _admit(ws, key="t"):
            return
        try:
            await _control(ws, device)
        finally:
            farm.sockets -= 1

    async def _control(ws: WebSocket, device) -> None:
        peer = f"{ws.client.host}:{ws.client.port}" if ws.client else "?"
        takeover = ws.query_params.get("takeover", "").lower() in ("1", "true", "yes", "on")
        while (other := farm.controls.get(device.id)) is not None:
            if not takeover and not other.closing:
                farm.log("control_refused", device=device.id, peer=peer, holder=other.peer)
                await ws.send_text(dumps({"t": "error", "code": "busy", "error": (
                    f"{device.id} is controlled live by another client ({other.peer}); connect with "
                    "?takeover=true to take over")}))
                await ws.close(code=4409, reason="live control is in use by another client")
                return
            if not other.closing:
                other.kicked.set()
            try:  # its session releases everything first
                await asyncio.wait_for(other.done.wait(), 10.0)
            except asyncio.TimeoutError:  # not the builtin TimeoutError before Python 3.11
                if farm.controls.get(device.id) is other:
                    del farm.controls[device.id]
        me = farm.controls[device.id] = _Control(peer)
        lock = asyncio.Lock()

        async def send(msg: dict) -> None:
            async with lock:
                await ws.send_text(dumps(msg))

        async def receiver() -> None:
            while True:
                msg = await ws.receive()
                if msg["type"] == "websocket.disconnect":
                    return
                try:
                    data = json.loads(msg.get("text") or "null")
                except ValueError:
                    data = None
                for item in data if isinstance(data, list) else [data]:
                    if isinstance(item, dict):
                        await session.handle(item)
                    else:
                        await send({"t": "error", "error": "messages must be JSON objects"})

        session = LiveSession(device, send)
        session.start()
        farm.log("ws_open", kind="control", device=device.id, peer=peer, takeover=takeover)
        try:
            st = device.status()
            await send({"t": "hello", "device": device.id, "mode": st["pointer"].get("mode", "relative"),
                        "tick_ms": round(session.tick * 1000, 1)})
            await _run_until_first_exits(receiver(), me.kicked.wait())
        except (WebSocketDisconnect, RuntimeError, OSError):
            pass
        finally:
            me.closing = True

            def ended(_) -> None:
                if farm.controls.get(device.id) is me:
                    del farm.controls[device.id]
                me.done.set()
                farm.log("ws_close", kind="control", device=device.id, kicked=me.kicked.is_set(), **dict(session.totals))

            # releases every key and button; a task of its own, so that it ends (and the device's
            # next control connection may start) even when this handler is cancelled
            closing = asyncio.ensure_future(session.close())
            closing.add_done_callback(ended)
            await asyncio.shield(closing)
        if me.kicked.is_set():
            try:
                await send({"t": "error", "code": "taken_over", "error": "live control was taken over by another client"})
                await ws.close(code=4409, reason="taken over by another client")
            except (WebSocketDisconnect, RuntimeError, OSError):
                pass

    async def _admit(ws: WebSocket, key: str = "type") -> bool:
        """Count a socket in, or close it (code 4429) when the server has too many."""
        if farm.sockets >= farm.max_websockets:
            farm.log("ws_refused", reason="too_many", path=ws.url.path)
            await ws.send_text(dumps({key: "error", "code": "too_many", "error": (
                f"this server already has {farm.sockets} WebSocket connections (the most allowed)")}))
            await ws.close(code=4429, reason="too many connections")
            return False
        farm.sockets += 1
        return True

    # -- simulator ------------------------------------------------------------------------------

    @app.post("/api/devices/{device_id}/sim/{op}", tags=["simulator"], summary="Simulated phone control",
              response_model=models.ActionResponse, responses={404: models.ERRORS[404], 422: models.ERRORS[422]})
    async def sim(op: SimOp, device_id: str = DeviceId,
                  name: Optional[str] = Query(None, description="App for open_app, e.g. Targets, Notes, Settings")):
        """What a person could do to a simulated phone: lock/unlock it, raise or answer the wired-accessory
        prompt, cut the HDMI signal, go home, open an app. The device's health is checked right after, so the
        returned `state` is current."""
        device = farm.device(device_id)
        rig = registry.extra(device_id)
        if rig is None or not hasattr(rig, "phone") or getattr(device.info, "kind", "") != "sim":
            raise ApiError(404, f"{device_id} is not a simulated device")

        def do() -> dict:
            phone = rig.phone
            if op in ("signal_off", "signal_on"):
                rig.capture.signal = op == "signal_on"
                device.frame(newer_than=device.frame().seq)  # the health check must see the new picture
            elif op == "home":
                phone.go_home()
            elif op == "open_app":
                if not name:
                    raise ValueError("open_app needs ?name=<app>")
                phone.open_app(name)
            else:
                getattr(phone, op)()
            device.check()
            return {"state": device.state, "health": device.status()["health"]}

        return _ok(await farm.on_io(do))

    def openapi() -> dict:
        """The generated schema, with every 422 documented as the API's own error body."""
        if app.openapi_schema is None:
            from fastapi.openapi.utils import get_openapi

            schema = get_openapi(title=app.title, version=app.version, description=app.description,
                                 routes=app.routes, tags=app.openapi_tags)
            error = {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}}
            for ops in schema.get("paths", {}).values():
                for op in ops.values():
                    if "422" in op.get("responses", {}):
                        op["responses"]["422"] = {"description": models.ERRORS[422]["description"], "content": error}
            for name in ("HTTPValidationError", "ValidationError"):
                schema.get("components", {}).get("schemas", {}).pop(name, None)
            if token:  # the docs' "Authorize" button
                schema.setdefault("components", {})["securitySchemes"] = {"token": {"type": "http", "scheme": "bearer"}}
                for path, ops in schema.get("paths", {}).items():
                    if path.startswith("/api/") and path != "/api/health" and not path.endswith("/calibration/events"):
                        for op in ops.values():
                            op["security"] = [{"token": []}]
            app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = openapi
    if web.is_dir():
        app.mount("/", _NoCacheStatic(directory=web, html=True), name="web")
    return app


async def _ws_device(ws: WebSocket, device_id: str, key: str = "type"):
    """The device, or None after telling the client it does not exist (close code 4404)."""
    farm: _Farm = ws.app.state.farm
    try:
        return farm.device(device_id)
    except ApiError as e:
        await ws.send_text(dumps({key: "error", "error": e.message, "code": "not_found"}))
        await ws.close(code=4404)
        return None


async def _run_until_first_exits(*coros) -> None:
    """Run coroutines together; when one returns or fails (e.g. the client left), cancel the rest."""
    tasks = [asyncio.ensure_future(c) for c in coros]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        results = await asyncio.gather(*tasks, return_exceptions=True)
    for r in results:
        if isinstance(r, Exception) and not isinstance(r, (WebSocketDisconnect, RuntimeError, OSError)):
            raise r
