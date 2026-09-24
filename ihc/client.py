"""Python SDK for the iphone-hid API (synchronous, httpx).

    from ihc.client import Farm

    farm = Farm("http://farm-01:8000", "http://farm-02:8000")   # one or more hosts
    farm = Farm.discover()                                      # or every box on the LAN (mDNS)
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
sent), 409 device error (HID, locked phone, pointer, calibration), 503 no video, 504 HID timeout;
None when the host could not be reached.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

__all__ = ["Farm", "IhcError", "RemoteDevice"]


class IhcError(Exception):
    """An API call failed.

    `status_code`: HTTP status (None if the host was unreachable); `code`: the API's error code
    (not_found, invalid_input, device_error, no_video, hid_timeout, internal); `payload`: the
    error body, or a failed script's step results."""

    def __init__(self, message: str, status_code: int | None = None, payload: Any = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.payload = payload

    @property
    def code(self) -> str | None:
        return self.payload.get("code") if isinstance(self.payload, dict) else None

    def __str__(self) -> str:
        return f"{self.message} (HTTP {self.status_code})" if self.status_code else self.message


class Host:
    """One API server: an httpx client plus the error mapping."""

    def __init__(self, base_url: str, timeout: float = 60):
        self.base_url = base_url.rstrip("/")
        self.http = httpx.Client(base_url=self.base_url, timeout=timeout)

    def request(self, method: str, path: str, *, timeout: float | None = None, **kwargs) -> httpx.Response:
        if timeout is not None:
            kwargs["timeout"] = timeout
        try:
            r = self.http.request(method, path, **kwargs)
        except httpx.HTTPError as e:
            raise IhcError(f"{self.base_url}: {e}") from e
        if r.status_code >= 400:
            try:
                payload = r.json()
                message = payload.get("error") or r.text
            except (ValueError, AttributeError):
                payload, message = None, r.text or r.reason_phrase
            raise IhcError(message, r.status_code, payload)
        return r

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

    def _action(self, name: str, timeout: float | None = None, **body) -> dict:
        return self._host.json("POST", self._path(f"/{name}"), json=body, timeout=timeout).get("result")

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

    def tap(self, x: float, y: float, space: str = "norm") -> dict:
        """Tap at (x, y)."""
        return self._action("tap", x=x, y=y, space=space)

    def long_press(self, x: float, y: float, space: str = "norm") -> dict:
        """Press and hold at (x, y) (0.8 s)."""
        return self._action("long_press", x=x, y=y, space=space)

    def move(self, x: float, y: float, space: str = "norm") -> dict:
        """Move the pointer to (x, y) without clicking."""
        return self._action("move", x=x, y=y, space=space)

    def swipe(self, x1: float, y1: float, x2: float, y2: float, hold_end: float = 0.0, space: str = "norm",
              duration: float | None = None) -> dict:
        """Press at (x1, y1), drag to (x2, y2), release. `hold_end` seconds of holding before the
        release turn a fling into a precise drag; `duration` applies to absolute-pointer phones."""
        body = {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "hold_end": hold_end, "space": space}
        if duration is not None:
            body["duration"] = duration
        return self._action("swipe", **body)

    def scroll(self, x: float, y: float, amount: int, space: str = "norm") -> dict:
        """Turn the wheel `amount` detents at (x, y); positive scrolls up."""
        return self._action("scroll", x=x, y=y, amount=amount, space=space)

    def type(self, text: str) -> dict:
        """Type text on the hardware keyboard (US layout: printable ASCII, \\n, \\t). Untypeable
        text is refused (422) before anything is sent."""
        return self._action("type", text=text)

    def key(self, combo: str) -> dict:
        """Press a key combination, e.g. "cmd+space", "esc", "shift+tab"."""
        return self._action("key", combo=combo)

    def home(self) -> dict:
        """Home (secondary mouse button in AssistiveTouch)."""
        return self._action("home")

    def app_switcher(self) -> dict:
        """App Switcher (middle mouse button in AssistiveTouch)."""
        return self._action("app_switcher")

    def media(self, key: str) -> dict:
        """Media key: volume_up, volume_down, mute, play_pause, next_track, prev_track..."""
        return self._action("media", key=key)

    def open_url(self, url: str) -> dict:
        """Open a URL in Safari (Spotlight, type, Return)."""
        return self._action("open_url", url=url)

    def release_all(self) -> dict:
        """Release every key and mouse button."""
        return self._action("release_all")

    def calibrate(self, page_url: str | None = None, timeout: float = 600.0, open_page: bool = True,
                  **options) -> dict:
        """Calibrate the pointer through the Safari calibration page (about a minute; the phone
        must reach the server, by default at <public_url>/calibrate/<id>). With open_page=False the
        page must already be open in Safari (typed by hand, e.g. when Spotlight does not open).
        `options` go to ihc.calibration.calibrate (validate, try_absolute, max_error, ...)."""
        body: dict[str, Any] = {"options": options} if options else {}
        if page_url:
            body["page_url"] = page_url
        if not open_page:
            body["open_page"] = False
        return self._action("calibrate", timeout=timeout, **body)

    def calibration(self) -> dict:
        """Current calibration summary (method, measured_at, validation error, pointer mode)."""
        return self._host.json("GET", self._path("/calibration"))

    def run(self, actions: list[dict], stop_on_error: bool = True, timeout: float | None = None) -> dict:
        """Run a script on the server, in order and without interleaving with other requests for
        this phone: [{"type": "tap", "x": .5, "y": .5}, {"type": "wait", "seconds": 1}, ...].
        Every step is validated first (IhcError 422 names the bad one). Returns the step results;
        raises IhcError (payload = the step results) when a step failed."""
        data = self._host.json("POST", self._path("/actions"), json={"actions": actions, "stop_on_error": stop_on_error},
                               timeout=timeout)
        if not data.get("ok"):
            raise IhcError(data.get("error") or "script failed", 200, data.get("result"))
        return data["result"]


class Farm:
    """The phones of one or more hosts: Farm("http://host-a:8000", "http://host-b:8000"), or every
    box on the local network: Farm.discover()."""

    def __init__(self, *base_urls: str, timeout: float = 60):
        if not base_urls:
            raise ValueError("Farm needs at least one base URL, e.g. Farm('http://host:8000')")
        self.hosts = [Host(u, timeout) for u in base_urls]

    @classmethod
    def discover(cls, wait: float = 2.0, timeout: float = 60) -> Farm:
        """Every box announcing itself on the LAN (mDNS service _ihc._tcp; needs the `zeroconf`
        package). IhcError when none answers within `wait` seconds."""
        from .discovery import discover

        urls = discover(wait)
        if not urls:
            raise IhcError(f"no ihc box answered on the local network within {wait:.0f} s "
                           "(is zeroconf installed, and are the boxes on this subnet?)")
        return cls(*urls, timeout=timeout)

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
