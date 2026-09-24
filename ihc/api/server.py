"""HTTP/WebSocket API and web console for the phones of one host.

    app = create_app(registry, public_url="http://192.168.1.20:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000, ws_per_message_deflate=False)

Concurrency model:
- the event loop never blocks: device actions run on one worker thread per device (so the requests
  of one phone run in arrival order and never hold up another phone), screenshots and status
  checks on an I/O pool, JPEG variants on an encoder pool;
- video comes from one pump thread per watched device (ihc.api.stream), shared by every viewer;
- each /control connection owns a worker thread that sends its HID reports in order
  (ihc.api.control), and releases everything when the connection ends.

Errors are JSON {"error": "..."}: 404 unknown device, 422 bad input, 409 device/HID errors,
503 no video, 504 HID timeouts.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Query, Request, WebSocket
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocketDisconnect

from ..hid.base import HidError, HidTimeout
from ..input.pointer import PointerError
from . import actions
from .control import LiveSession, dumps
from .stream import Hubs, MJPEGResponse, Pacer

WEB_DIR = Path(__file__).resolve().parents[2] / "web"
STATUS_INTERVAL = 0.5
ACK_TIMEOUT = 2.0
SIM_OPS = ("lock", "unlock", "prompt_accessory", "allow_accessory", "signal_off", "signal_on", "home", "open_app")
EVENT_TYPES = ("hello", "click", "move")


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


def _message(exc: BaseException) -> str:
    text = actions.describe(exc) if isinstance(exc, ValueError) else str(exc)
    if isinstance(exc, OSError) and not isinstance(exc, HidError):
        text = f"no video: {text}" if text else "no video"
    return text or type(exc).__name__


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
            if isinstance(v, str):
                return v.lower() in ("1", "true", "yes", "on")
            return bool(v)

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


class _Farm:
    """Server state: the registry plus the executors and video hubs built on top of it."""

    def __init__(self, registry, public_url: str | None, log):
        self.registry = registry
        self.public_url = public_url.rstrip("/") if public_url else None
        self.log = log or (lambda event, **fields: None)
        self.started = time.time()
        cpus = os.cpu_count() or 2
        self.io = ThreadPoolExecutor(max(8, 2 * cpus), thread_name_prefix="ihc-io")
        self.encoder = ThreadPoolExecutor(max(2, cpus), thread_name_prefix="ihc-jpeg")
        self.hubs = Hubs(self.encoder)
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

    async def on_device(self, device, fn, *args) -> Any:
        """Run a blocking device action on the device's own worker (in arrival order)."""
        return await self._call(self.worker(device), fn, *args)

    async def on_io(self, fn, *args) -> Any:
        return await self._call(self.io, fn, *args)

    @staticmethod
    async def _call(executor, fn, *args) -> Any:
        try:
            return await asyncio.get_running_loop().run_in_executor(executor, fn, *args)
        except ApiError:
            raise
        except Exception as e:
            raise ApiError(error_status(e), _message(e)) from e

    def base_url(self, request: Request) -> str:
        return self.public_url or str(request.base_url).rstrip("/")

    def close(self) -> None:
        self.hubs.close()
        for ex in [*self._workers.values(), self.io, self.encoder]:
            ex.shutdown(wait=False, cancel_futures=True)


async def _json_body(request: Request, default: Any = None) -> Any:
    raw = await request.body()
    if not raw.strip():
        return default
    try:
        return json.loads(raw)
    except ValueError:
        raise ApiError(422, "the body is not valid JSON") from None


def _ok(result: Any = None) -> dict:
    return {"ok": True, "result": result}


def _validate_event(ev: Any) -> dict:
    if not isinstance(ev, dict) or ev.get("type") not in EVENT_TYPES:
        raise ApiError(422, f"an event must be an object with type one of {', '.join(EVENT_TYPES)}")
    need = ("screen_w", "screen_h", "inner_w", "inner_h") if ev["type"] == "hello" else ("x", "y")
    for key in need:
        v = ev.get(key)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            raise ApiError(422, f"{ev['type']} event needs a number {key!r}")
    return ev


def create_app(registry, *, public_url: str | None = None, log=None, web_dir: str | Path | None = None) -> FastAPI:
    """The API and web console for the devices of `registry` (which the caller keeps and closes).

    `public_url` is how phones reach this server (the calibration page is opened from it); by
    default the URL of each request is used."""
    farm = _Farm(registry, public_url, log)
    web = Path(web_dir) if web_dir else WEB_DIR

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            yield
        finally:
            farm.close()

    app = FastAPI(title="iphone-hid", version="0.1", lifespan=lifespan)
    app.state.farm = farm

    # -- errors ---------------------------------------------------------------------------------

    @app.exception_handler(ApiError)
    async def _api_error(request, exc: ApiError):
        return JSONResponse({"error": exc.message}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request, exc: RequestValidationError):
        parts = []
        for err in exc.errors():
            loc = ".".join(str(p) for p in err.get("loc", ()) if p not in ("body", "query", "path"))
            parts.append(f"{loc}: {err.get('msg', 'invalid')}" if loc else err.get("msg", "invalid"))
        return JSONResponse({"error": "; ".join(parts) or "invalid request"}, status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request, exc: StarletteHTTPException):
        return JSONResponse({"error": str(exc.detail)}, status_code=exc.status_code, headers=getattr(exc, "headers", None))

    @app.exception_handler(Exception)
    async def _internal_error(request, exc: Exception):
        farm.log("api_error", path=request.url.path, error=repr(exc))
        return JSONResponse({"error": f"internal error: {exc}"}, status_code=500)

    # -- devices --------------------------------------------------------------------------------

    @app.get("/api/health")
    async def health():
        return {"ok": True, "devices": len(registry.devices()), "uptime_s": round(time.time() - farm.started, 1),
                "public_url": farm.public_url}

    @app.get("/api/devices")
    async def devices():
        return {"devices": [d.status() for d in registry.devices()]}

    @app.get("/api/devices/{device_id}")
    async def device_status(device_id: str):
        return farm.device(device_id).status()

    @app.get("/api/devices/{device_id}/calibration")
    async def calibration(device_id: str):
        st = farm.device(device_id).status()
        return {**st["calibration"], "mode": st["pointer"].get("mode", "relative")}

    # -- video ----------------------------------------------------------------------------------

    @app.get("/api/devices/{device_id}/screenshot")
    async def screenshot(device_id: str, format: Literal["jpeg", "jpg", "png"] = "jpeg", crop: bool = True,
                         quality: int = Query(85, ge=10, le=100)):
        device = farm.device(device_id)
        fmt = "png" if format == "png" else "jpeg"
        data = await farm.on_io(lambda: device.screenshot(fmt=fmt, crop=crop, quality=quality))
        return Response(data, media_type=f"image/{fmt}", headers={"Cache-Control": "no-store"})

    @app.get("/api/devices/{device_id}/mjpeg")
    async def mjpeg(device_id: str, fps: float = Query(0.0, ge=0, le=120), crop: bool = False,
                    width: int | None = Query(None, ge=16, le=4096), quality: int = Query(80, ge=10, le=95),
                    frames: int | None = Query(None, ge=1)):
        """multipart/x-mixed-replace JPEG stream. Without crop and width the capture card's own
        JPEG bytes are forwarded untouched; `frames` ends the stream after that many parts."""
        device = farm.device(device_id)
        return MJPEGResponse(farm.hubs.get(device), fps=fps, crop=crop, width=width, quality=quality, frames=frames)

    @app.websocket("/api/devices/{device_id}/stream")
    async def stream(ws: WebSocket, device_id: str):
        """Binary messages: JPEG frames (the card's own bytes unless crop/width is asked for).
        Text messages: {"type": "status", ...} about twice a second. The client may send
        {"fps", "crop", "width", "quality", "ack"}; with ack on, it answers every frame with
        {"t": "ack"} and never has more than one frame in flight."""
        await ws.accept()
        device = await _ws_device(ws, device_id)
        if device is None:
            return
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

        async def sender() -> None:
            loop = asyncio.get_running_loop()
            pacer = Pacer(settings.fps)
            seen = last = None
            last_age_ms = None
            sent, sent_at = 0, deque(maxlen=120)
            next_status = 0.0
            while True:
                now = loop.time()
                if now >= next_status:
                    next_status = now + STATUS_INTERVAL
                    recent = [t for t in sent_at if now - t <= 2.0]
                    st = device.status()
                    await send_text({
                        "type": "status", **st,
                        "frame": None if last is None else {"seq": last.seq, "ts": last.ts, "width": last.width,
                                                            "height": last.height, "age_ms": last_age_ms},
                        "screen_rect": device.screen_rect().to_dict(),
                        "stream": {"fps": round(len(recent) / 2.0, 1), "source_fps": round(hub.fps, 1), "sent": sent,
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
                        except TimeoutError:
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

        farm.log("ws_open", kind="stream", device=device.id)
        async with hub.watch() as w:
            await _run_until_first_exits(receiver(), sender())
        farm.log("ws_close", kind="stream", device=device.id)

    # -- actions --------------------------------------------------------------------------------

    def add_action(name: str, model: type[actions.Body], run) -> None:
        async def endpoint(device_id: str, request: Request):
            device = farm.device(device_id)
            params = await _json_body(request, {})
            if not isinstance(params, dict):
                raise ApiError(422, "the body must be a JSON object")
            try:
                body = model.model_validate(params)
            except ValueError as e:
                raise ApiError(422, actions.describe(e)) from None
            return _ok(await farm.on_device(device, run, device, body))

        endpoint.__name__ = f"action_{name}"
        schema = model.model_json_schema()
        app.add_api_route(
            f"/api/devices/{{device_id}}/{name}", endpoint, methods=["POST"], name=f"action_{name}",
            summary=name, openapi_extra={"requestBody": {"required": bool(schema.get("required")),
                                                         "content": {"application/json": {"schema": schema}}}},
        )

    for _name, (_model, _run) in actions.ACTIONS.items():
        add_action(_name, _model, _run)

    @app.post("/api/devices/{device_id}/actions")
    async def run_script(device_id: str, request: Request):
        """Run a recorded script: {"actions": [{"type": "tap", "x": .5, "y": .5}, {"type": "wait",
        "seconds": 1}, ...], "stop_on_error": true}. Every step is validated before the first runs."""
        device = farm.device(device_id)
        body = await _json_body(request)
        if isinstance(body, list):
            body = {"actions": body}
        if not isinstance(body, dict) or not isinstance(body.get("actions"), list):
            raise ApiError(422, 'the body must be {"actions": [...], "stop_on_error": true}')
        stop_on_error = body.get("stop_on_error", True)
        if not isinstance(stop_on_error, bool):
            raise ApiError(422, "stop_on_error must be true or false")
        steps = []
        for i, step in enumerate(body["actions"]):
            if not isinstance(step, dict):
                raise ApiError(422, f"actions[{i}] must be an object")
            kind = step.get("type")
            try:
                run, parsed = actions.parse(kind, {k: v for k, v in step.items() if k != "type"}, script=True)
            except ValueError as e:
                raise ApiError(422, f"actions[{i}] ({kind}): {actions.describe(e)}") from None
            steps.append((kind, run, parsed))

        def run_all() -> dict:
            results, t0 = [], time.monotonic()
            for i, (kind, run, parsed) in enumerate(steps):
                s0 = time.monotonic()
                try:
                    result = run(device, parsed)
                    results.append({"index": i, "type": kind, "ok": True, "result": result,
                                    "seconds": round(time.monotonic() - s0, 3)})
                except Exception as e:
                    results.append({"index": i, "type": kind, "ok": False, "error": _message(e),
                                    "status": error_status(e), "seconds": round(time.monotonic() - s0, 3)})
                    if stop_on_error:
                        break
            return {"steps": results, "completed": sum(r["ok"] for r in results), "total": len(steps),
                    "seconds": round(time.monotonic() - t0, 3)}

        out = await farm.on_device(device, run_all)
        failed = [r for r in out["steps"] if not r["ok"]]
        if failed:
            f = failed[0]
            return {"ok": False, "error": f"step {f['index']} ({f['type']}): {f['error']}", "result": out}
        return _ok(out)

    @app.post("/api/devices/{device_id}/calibrate")
    async def calibrate(device_id: str, request: Request):
        """Open the calibration page in Safari (through Spotlight) and measure the pointer; takes
        about a minute. The phone must reach `public_url` (same network)."""
        device = farm.device(device_id)
        params = await _json_body(request, {})
        try:
            body = actions.Calibrate.model_validate(params if isinstance(params, dict) else None)
        except ValueError as e:
            raise ApiError(422, actions.describe(e)) from None
        page_url = body.page_url or f"{farm.base_url(request)}/calibrate/{device.id}"
        farm.log("calibrate_start", device=device.id, page_url=page_url)
        result = await farm.on_device(device, lambda: device.calibrate(page_url=page_url, **body.options))
        return _ok({**result, "page_url": page_url})

    @app.post("/api/devices/{device_id}/calibration/events")
    async def calibration_events(device_id: str, request: Request):
        """Events from the calibration page (one object, a list, or {"events": [...]}), in order."""
        device = farm.device(device_id)
        body = await _json_body(request)
        events = body.get("events") if isinstance(body, dict) and "events" in body else body
        events = events if isinstance(events, list) else [events]
        for ev in events:
            _validate_event(ev)
        for ev in events:
            device.clicks.push(ev)
        return _ok({"accepted": len(events)})

    @app.get("/calibrate/{device_id}", include_in_schema=False)
    async def calibration_page(device_id: str):
        farm.device(device_id)
        return FileResponse(web / "calibrate.html", media_type="text/html",
                            headers={"Cache-Control": "no-cache, no-store, must-revalidate"})

    # -- live control ---------------------------------------------------------------------------

    @app.websocket("/api/devices/{device_id}/control")
    async def control(ws: WebSocket, device_id: str):
        """KVM-style control: {"t": "mouse", "dx", "dy", "buttons", "wheel"}, {"t": "abs", "x",
        "y", "buttons"}, {"t": "keys", "mods", "keys"}, {"t": "release"}, actions as {"t": "tap",
        "id": 1, ...} (answered with {"t": "result", "id", "ok", ...}) and {"t": "sync", "id"}
        (answered once everything before it was sent). See ihc.api.control."""
        await ws.accept()
        device = await _ws_device(ws, device_id, key="t")
        if device is None:
            return
        lock = asyncio.Lock()

        async def send(msg: dict) -> None:
            async with lock:
                await ws.send_text(dumps(msg))

        session = LiveSession(device, send)
        session.start()
        farm.log("ws_open", kind="control", device=device.id)
        try:
            st = device.status()
            await send({"t": "hello", "device": device.id, "mode": st["pointer"].get("mode", "relative"),
                        "tick_ms": round(session.tick * 1000, 1)})
            while True:
                msg = await ws.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                try:
                    data = json.loads(msg.get("text") or "null")
                except ValueError:
                    data = None
                batch = data if isinstance(data, list) else [data]
                for item in batch:
                    if isinstance(item, dict):
                        await session.handle(item)
                    else:
                        await send({"t": "error", "error": "messages must be JSON objects"})
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            await session.close()  # releases every key and button
            farm.log("ws_close", kind="control", device=device.id, **{k: v for k, v in session.totals.items()})

    # -- simulator ------------------------------------------------------------------------------

    @app.post("/api/devices/{device_id}/sim/{op}")
    async def sim(device_id: str, op: str, name: str | None = None):
        """Operator controls of a simulated phone (lock, accessory prompt, HDMI signal, apps)."""
        device = farm.device(device_id)
        rig = registry.extra(device_id)
        if rig is None or not hasattr(rig, "phone") or device.info.kind != "sim":
            raise ApiError(404, f"{device_id} is not a simulated device")
        if op not in SIM_OPS:
            raise ApiError(404, f"unknown simulator control {op!r}; one of {', '.join(SIM_OPS)}")

        def do() -> dict:
            phone = rig.phone
            if op == "signal_off" or op == "signal_on":
                rig.capture.signal = op == "signal_on"
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

    if web.is_dir():
        app.mount("/", _NoCacheStatic(directory=web, html=True), name="web")
    return app


async def _ws_device(ws: WebSocket, device_id: str, key: str = "type"):
    """The device, or None after telling the client it does not exist (close code 4404)."""
    farm: _Farm = ws.app.state.farm
    try:
        return farm.device(device_id)
    except ApiError as e:
        await ws.send_text(dumps({key: "error", "error": e.message}))
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
        if isinstance(r, Exception) and not isinstance(r, (WebSocketDisconnect, RuntimeError, OSError, asyncio.CancelledError)):
            raise r
