"""Python SDK for the iphone-hid API (synchronous, httpx).

    from ihc.client import Farm

    farm = Farm("http://farm-01:8000", "http://farm-02:8000")   # one or more hosts
    farm = Farm.discover()                                      # or every box on the LAN (mDNS)
    farm = Farm.discover(token="...")                           # boxes with an API token (default $IHC_TOKEN)
    for phone in farm.devices():
        print(phone.id, phone.model, phone.state)

    phone = farm.device("iphone-01")
    phone.home()
    phone.tap(0.50, 0.93)                  # normalized 0..1 screen coordinates
    phone.swipe(0.5, 0.8, 0.5, 0.2)
    phone.key("cmd+space")                 # Spotlight
    phone.type("notes\\n")
    phone.screenshot("shot.png")
    phone.run([{"type": "tap", "x": 0.5, "y": 0.5}, {"type": "wait", "seconds": 1}])

Every action blocks until the phone has received it and returns the device's report. A refused or
failed call raises IhcError: `status_code` 404 unknown device, 422 invalid input (nothing was
sent), 409 device error (HID, locked phone, pointer, calibration), 503 no video, 504 HID timeout,
401 missing or wrong token; None when the host could not be reached.

Timeouts grow with the work (typed text, script waits), on top of the `timeout` for the queue and
the network. Every action carries an Idempotency-Key: retrying it with the same key (the failed
call's `IhcError.idempotency_key`, passed as `idempotency_key=`) never makes the phone act twice;
the server returns the first attempt's outcome, waiting for it if it still runs.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any, Mapping

import httpx

STEP_S = 5.0  # time allowed per action on top of the base timeout (a relative tap takes ~1 s on hardware)
TYPE_S_PER_CHAR = 0.1  # typing: a key press and release (~40 ms at 9600 baud), with a wide margin

__all__ = ["Farm", "IhcError", "RemoteDevice"]


class IhcError(Exception):
    """An API call failed.

    `status_code`: HTTP status (None if the host was unreachable); `code`: the API's error code
    (not_found, invalid_input, device_error, no_video, hid_timeout, unauthorized, internal...);
    `payload`: the error body, or a failed script's step results; `idempotency_key`: the key the
    failed action was sent with (pass it as `idempotency_key=` to retry it safely)."""

    def __init__(self, message: str, status_code: int | None = None, payload: Any = None,
                 idempotency_key: str | None = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.payload = payload
        self.idempotency_key = idempotency_key

    @property
    def code(self) -> str | None:
        return self.payload.get("code") if isinstance(self.payload, dict) else None

    def __str__(self) -> str:
        return f"{self.message} (HTTP {self.status_code})" if self.status_code else self.message


class Host:
    """One API server: an httpx client plus the error mapping."""

    def __init__(self, base_url: str, timeout: float = 60, token: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.http = httpx.Client(base_url=self.base_url, timeout=timeout, headers=headers)

    def request(self, method: str, path: str, *, timeout: float | None = None, **kwargs) -> httpx.Response:
        if timeout is not None:
            kwargs["timeout"] = timeout
        key = (kwargs.get("headers") or {}).get("Idempotency-Key")
        try:
            r = self.http.request(method, path, **kwargs)
        except httpx.HTTPError as e:
            raise IhcError(f"{self.base_url}: {e}", idempotency_key=key) from e
        if r.status_code >= 400:
            try:
                payload = r.json()
                message = payload.get("error") or r.text
            except (ValueError, AttributeError):
                payload, message = None, r.text or r.reason_phrase
            if r.status_code == 401:
                message = f"{self.base_url}: {message} (pass token= or set IHC_TOKEN)"
            raise IhcError(message, r.status_code, payload, key)
        return r

    def post(self, path: str, body: dict, *, work: float = 0.0, timeout: float | None = None,
             idempotency_key: str | None = None) -> Any:
        """POST an action: JSON body (even when empty), an Idempotency-Key (fresh unless given),
        and a timeout of the base timeout plus `work` seconds unless `timeout` is given."""
        headers = {"Idempotency-Key": idempotency_key or uuid.uuid4().hex}
        return self.json("POST", path, json=body, headers=headers,
                         timeout=timeout if timeout is not None else self.timeout + work)

    def json(self, method: str, path: str, **kwargs) -> Any:
        return self.request(method, path, **kwargs).json()

    def devices(self) -> list[RemoteDevice]:
        return [RemoteDevice(self, info) for info in self.json("GET", "/api/devices")["devices"]]

    def close(self) -> None:
        self.http.close()


class RemoteDevice:
    """One phone on one host.

    Coordinates are normalized by default: (0, 0) top-left, (1, 1) bottom-right of the screen;
    pass space="pt" for iOS points or space="frame" for pixels of the captured video."""

    def __init__(self, host: Host, info: dict):
        self._host = host
        self.id: str = info["id"]
        self.info = info  # status when this object was made or last refreshed by status()

    def __repr__(self) -> str:
        return f"RemoteDevice({self.id!r} @ {self.host})"

    @property
    def host(self) -> str:
        """Base URL of the server this phone is attached to."""
        return self._host.base_url

    @property
    def model(self) -> str:
        return self.info.get("model") or ""

    @property
    def kind(self) -> str:
        """"hardware" or "sim"."""
        return self.info.get("kind") or ""

    @property
    def state(self) -> str:
        """Current state, fetched: ready, busy, hid_disconnected (locked phone, accessory prompt,
        cable), hid_offline (chip unreachable) or no_signal (no video)."""
        return self.status()["state"]

    def _path(self, tail: str = "") -> str:
        return f"/api/devices/{self.id}{tail}"

    def _action(self, name: str, *, timeout: float | None = None, idempotency_key: str | None = None, **body) -> dict:
        return self._host.post(self._path(f"/{name}"), body, work=work({"type": name, **body}), timeout=timeout,
                               idempotency_key=idempotency_key).get("result")

    def status(self) -> dict:
        """Full status: state, health, screen geometry, pointer (mode, position), calibration, HID
        counters and the last action's result."""
        self.info = self._host.json("GET", self._path())
        return self.info

    def screenshot(self, path: str | Path | None = None, crop: bool = True, format: str = "png") -> bytes:
        """The screen as PNG or JPEG bytes, cropped to the phone (crop=False: the whole capture
        frame); also written to `path` when given."""
        data = self._host.request("GET", self._path("/screenshot"),
                                  params={"format": format, "crop": str(bool(crop)).lower()}).content
        if path is not None:
            Path(path).write_bytes(data)
        return data

    def tap(self, x: float, y: float, space: str = "norm", *, idempotency_key: str | None = None) -> dict:
        """Tap at (x, y)."""
        return self._action("tap", x=x, y=y, space=space, idempotency_key=idempotency_key)

    def long_press(self, x: float, y: float, space: str = "norm", *, idempotency_key: str | None = None) -> dict:
        """Press and hold at (x, y) (0.8 s)."""
        return self._action("long_press", x=x, y=y, space=space, idempotency_key=idempotency_key)

    def move(self, x: float, y: float, space: str = "norm", *, idempotency_key: str | None = None) -> dict:
        """Move the pointer to (x, y) without clicking."""
        return self._action("move", x=x, y=y, space=space, idempotency_key=idempotency_key)

    def swipe(self, x1: float, y1: float, x2: float, y2: float, hold_end: float = 0.0, space: str = "norm",
              duration: float | None = None, *, idempotency_key: str | None = None) -> dict:
        """Press at (x1, y1), drag to (x2, y2), release. `hold_end` seconds of holding before the
        release turn a fling into a precise drag; `duration` applies to absolute-pointer phones."""
        body = {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "hold_end": hold_end, "space": space}
        if duration is not None:
            body["duration"] = duration
        return self._action("swipe", idempotency_key=idempotency_key, **body)

    def scroll(self, x: float, y: float, amount: int, space: str = "norm", *, idempotency_key: str | None = None) -> dict:
        """Turn the wheel `amount` detents at (x, y); positive scrolls up."""
        return self._action("scroll", x=x, y=y, amount=amount, space=space, idempotency_key=idempotency_key)

    def type(self, text: str, *, idempotency_key: str | None = None) -> dict:
        """Type text on the hardware keyboard (US layout: printable ASCII, \\n, \\t). Untypeable
        text is refused (422) before anything is sent."""
        return self._action("type", text=text, idempotency_key=idempotency_key)

    def key(self, combo: str, *, idempotency_key: str | None = None) -> dict:
        """Press a key combination, e.g. "cmd+space", "esc", "shift+tab"."""
        return self._action("key", combo=combo, idempotency_key=idempotency_key)

    def home(self, *, idempotency_key: str | None = None) -> dict:
        """Home (secondary mouse button in AssistiveTouch)."""
        return self._action("home", idempotency_key=idempotency_key)

    def app_switcher(self, *, idempotency_key: str | None = None) -> dict:
        """App Switcher (middle mouse button in AssistiveTouch)."""
        return self._action("app_switcher", idempotency_key=idempotency_key)

    def media(self, key: str, *, idempotency_key: str | None = None) -> dict:
        """Media key: volume_up, volume_down, mute, play_pause, next_track, prev_track..."""
        return self._action("media", key=key, idempotency_key=idempotency_key)

    def open_url(self, url: str, *, idempotency_key: str | None = None) -> dict:
        """Open a URL in Safari (Spotlight, type, Return)."""
        return self._action("open_url", url=url, idempotency_key=idempotency_key)

    def release_all(self, *, idempotency_key: str | None = None) -> dict:
        """Release every key and mouse button."""
        return self._action("release_all", idempotency_key=idempotency_key)

    def calibrate(self, page_url: str | None = None, timeout: float = 600.0, open_page: bool = True, *,
                  idempotency_key: str | None = None, **options) -> dict:
        """Calibrate the pointer through the Safari calibration page (about a minute; the phone
        must reach the server, by default at <public_url>/calibrate/<id>). With open_page=False the
        page must already be open in Safari, from the `page_url` calibration() gives (typed by
        hand, e.g. when Spotlight does not open). `options` go to ihc.calibration.calibrate
        (validate, try_absolute, max_error, ...)."""
        body: dict[str, Any] = {"options": options} if options else {}
        if page_url:
            body["page_url"] = page_url
        if not open_page:
            body["open_page"] = False
        return self._host.post(self._path("/calibrate"), body, timeout=timeout,
                               idempotency_key=idempotency_key).get("result")

    def set_pointer_mode(self, mode: str) -> dict:
        """"absolute" or "relative", without a calibration: absolute for a phone seen to follow
        absolute reports (the whole report range over the whole screen). Returns calibration()."""
        return self._host.post(self._path("/pointer"), {"mode": mode})

    def calibration(self) -> dict:
        """Current calibration summary (method, measured_at, validation error, pointer mode) and
        `page_url`: the calibration page with its current key, to open it by hand."""
        return self._host.json("GET", self._path("/calibration"))

    def run(self, actions: list[dict], stop_on_error: bool = True, timeout: float | None = None, *,
            idempotency_key: str | None = None) -> dict:
        """Run a script on the server, in order and without interleaving with other requests for
        this phone: [{"type": "tap", "x": .5, "y": .5}, {"type": "wait", "seconds": 1}, ...].
        Every step is validated first (IhcError 422 names the bad one). Returns the step results;
        raises IhcError (payload = the step results) when a step failed. The timeout is the base
        timeout plus the script's waits, typing and a few seconds per step, unless given."""
        data = self._host.post(self._path("/actions"), {"actions": actions, "stop_on_error": stop_on_error},
                               work=sum(work(a) for a in actions), timeout=timeout, idempotency_key=idempotency_key)
        if not data.get("ok"):
            raise IhcError(data.get("error") or "script failed", 200, data.get("result"))
        return data["result"]


def work(action: dict) -> float:
    """Seconds an action may take on the phone, beyond the base timeout (a generous estimate)."""
    try:
        kind = action.get("type")
        if kind == "wait":
            return max(0.0, float(action.get("seconds") or 0))
        if kind == "type":
            return STEP_S + TYPE_S_PER_CHAR * len(action.get("text") or "")
        if kind == "open_url":
            return 2 * STEP_S + TYPE_S_PER_CHAR * len(action.get("url") or "")
        if kind == "swipe":
            return STEP_S + max(0.0, float(action.get("hold_end") or 0)) + max(0.0, float(action.get("duration") or 0))
    except (TypeError, ValueError, AttributeError):  # the server refuses it anyway
        pass
    return STEP_S


def _token_for(token: str | Mapping[str, str] | None, base_url: str) -> str | None:
    if token is None:
        return os.environ.get("IHC_TOKEN") or None
    if isinstance(token, str):
        return token or None
    return {url.rstrip("/"): t for url, t in token.items()}.get(base_url.rstrip("/")) or None


def _answers(base_url: str, timeout: float = 2.0) -> bool:
    try:
        return httpx.get(f"{base_url.rstrip('/')}/api/health", timeout=timeout).status_code == 200
    except httpx.HTTPError:
        return False


class Farm:
    """The phones of one or more hosts: Farm("http://host-a:8000", "http://host-b:8000"), or every
    box on the local network: Farm.discover().

    `token`: the API token of the hosts, or {base URL: token} when they differ; by default
    $IHC_TOKEN (a box keeps its token in /var/lib/ihc/token)."""

    def __init__(self, *base_urls: str, timeout: float = 60, token: str | Mapping[str, str] | None = None):
        if not base_urls:
            raise ValueError("Farm needs at least one base URL, e.g. Farm('http://host:8000')")
        self.hosts = [Host(u, timeout, _token_for(token, u)) for u in base_urls]

    @classmethod
    def discover(cls, wait: float = 2.0, timeout: float = 60, token: str | Mapping[str, str] | None = None) -> Farm:
        """Every box announcing itself on the LAN (mDNS service _ihc._tcp; needs the `zeroconf`
        package). IhcError when none answers within `wait` seconds."""
        from .discovery import discover

        urls = [u for u in discover(wait) if _answers(u)]  # an announcement can outlive its box
        if not urls:
            raise IhcError(f"no ihc box answered on the local network within {wait:.0f} s "
                           "(is zeroconf installed, and are the boxes on this subnet?)")
        return cls(*urls, timeout=timeout, token=token)

    @property
    def urls(self) -> list[str]:
        return [h.base_url for h in self.hosts]

    def devices(self) -> list[RemoteDevice]:
        """Every device of every host, in host order."""
        return [d for host in self.hosts for d in host.devices()]

    def device(self, device_id: str) -> RemoteDevice:
        """The device with this id on the first host that has it (IhcError 404 if none)."""
        for host in self.hosts:
            try:
                return RemoteDevice(host, host.json("GET", f"/api/devices/{device_id}"))
            except IhcError as e:
                if e.status_code != 404:
                    raise
        raise IhcError(f"no device {device_id!r} on {', '.join(self.urls)}", 404, {"code": "not_found"})

    def close(self) -> None:
        for host in self.hosts:
            host.close()

    def __enter__(self) -> Farm:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
