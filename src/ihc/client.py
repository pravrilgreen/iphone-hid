"""Python SDK for iphone-hid boxes.

    from ihc import Farm

    farm = Farm("http://box-01.local:8000", token="...")   # one or more boxes
    farm = Farm.discover()                                 # or every box on the LAN (mDNS)
    phone = farm.devices()[0]

    phone.tap(0.5, 0.93)                  # coordinates are fractions of the screen, 0 to 1
    phone.swipe(0.5, 0.8, 0.5, 0.2)
    phone.drag(0.2, 0.3, 0.7, 0.3)        # hold, move, rest, lift: drag and drop
    phone.type("hello\\n")
    phone.key("cmd+space")
    phone.home()
    phone.screenshot("shot.png")

Each action returns once the phone has taken its last report. A refused or failed call raises
IhcError with the HTTP status and the box's error code: 400 bad_request (nothing was sent),
401 unauthorized, 404 no_device, 503 no_usb, asleep or no_video.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

import httpx

__all__ = ["Farm", "IhcError", "Phone"]


class IhcError(Exception):
    """An API call failed. `status_code` is None when the box could not be reached; `code` is the
    box's error code (bad_request, unauthorized, no_device, no_usb, asleep, no_video, failed)."""

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


class Box:
    """One box: an HTTP client and the error mapping."""

    def __init__(self, base_url: str, timeout: float = 30, token: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.http = httpx.Client(base_url=self.base_url, timeout=timeout, headers=headers)

    def request(self, method: str, path: str, **kwargs) -> httpx.Response:
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
            if r.status_code == 401:
                message = f"{self.base_url}: {message} (pass token= or set IHC_TOKEN)"
            raise IhcError(message, r.status_code, payload)
        return r

    def json(self, method: str, path: str, **kwargs) -> Any:
        return self.request(method, path, **kwargs).json()

    def phones(self) -> list[Phone]:
        return [Phone(self, info) for info in self.json("GET", "/api/devices")["devices"]]

    def close(self) -> None:
        self.http.close()


class Phone:
    """The iPhone of one box. Coordinates are fractions of the screen: (0, 0) top left, (1, 1)
    bottom right, whatever the phone model."""

    def __init__(self, box: Box, info: dict):
        self._box = box
        self.id: str = info["id"]
        self.info = info  # the status when this object was made, or last refreshed by status()

    def __repr__(self) -> str:
        return f"Phone({self.id!r} @ {self.url})"

    @property
    def url(self) -> str:
        """The box's base URL."""
        return self._box.base_url

    @property
    def state(self) -> str:
        """ready, busy, no_usb (not connected), asleep or no_video, fetched now."""
        return self.status()["state"]

    def _path(self, tail: str = "") -> str:
        return f"/api/devices/{self.id}{tail}"

    def _action(self, action: str, seconds: float = 0.0, /, **body: Any) -> dict:
        body = {k: v for k, v in body.items() if v is not None}
        timeout = self._box.timeout + seconds
        return self._box.json("POST", self._path(f"/{action}"), json=body, timeout=timeout).get("result", {})

    def status(self) -> dict:
        """The box's status: state, USB link, video, input latency, last action."""
        self.info = self._box.json("GET", self._path())
        return self.info

    def screenshot(self, path: str | Path | None = None, format: str = "png", wait: bool = False) -> bytes:
        """The screen as PNG (or format="jpeg"), also written to `path` when given. wait=True waits
        for a frame captured after the call."""
        params = {"format": format}
        if wait:
            params["wait"] = "true"
        data = self._box.request("GET", self._path("/screenshot"), params=params).content
        if path is not None:
            Path(path).write_bytes(data)
        return data

    def tap(self, x: float, y: float, hold_ms: int | None = None) -> dict:
        """Tap (x, y)."""
        return self._action("tap", x=x, y=y, hold_ms=hold_ms)

    def long_press(self, x: float, y: float, duration_ms: int | None = None) -> dict:
        """Touch and hold (x, y), 1.5 s by default."""
        return self._action("long_press", (duration_ms or 1500) / 1000, x=x, y=y, duration_ms=duration_ms)

    def swipe(self, x1: float, y1: float, x2: float, y2: float, duration_ms: int | None = None) -> dict:
        """Swipe from (x1, y1) to (x2, y2), lifting while moving: lists keep the swipe's speed."""
        return self._action("swipe", (duration_ms or 250) / 1000, x1=x1, y1=y1, x2=x2, y2=y2, duration_ms=duration_ms)

    def drag(self, x1: float, y1: float, x2: float, y2: float, hold_ms: int | None = None,
             duration_ms: int | None = None, rest_ms: int | None = None) -> dict:
        """Drag and drop: touch (x1, y1), hold (600 ms lifts an icon), move to (x2, y2), stay still,
        lift. Nothing keeps scrolling after it."""
        seconds = ((hold_ms or 600) + (duration_ms or 600) + (rest_ms or 200)) / 1000
        return self._action("drag", seconds, x1=x1, y1=y1, x2=x2, y2=y2, hold_ms=hold_ms, duration_ms=duration_ms,
                            rest_ms=rest_ms)

    def scroll(self, x: float, y: float, lines: int) -> dict:
        """Turn the scroll wheel at (x, y); positive scrolls towards the top."""
        return self._action("scroll", x=x, y=y, lines=lines)

    def type(self, text: str) -> dict:
        """Type text (printable ASCII, newline, tab; the phone uses the U.S. keyboard layout). Text
        that cannot be typed is refused before anything is sent."""
        return self._action("type", 0.1 * len(text), text=text)

    def key(self, combo: str) -> dict:
        """Press a key combination: "cmd+space", "esc", "shift+tab"."""
        return self._action("key", combo=combo)

    def button(self, name: str) -> dict:
        """Press a phone button: home, app_switcher, spotlight, volume_up, volume_down, mute, play_pause."""
        return self._action("button", name=name)

    def home(self) -> dict:
        return self.button("home")

    def app_switcher(self) -> dict:
        return self.button("app_switcher")

    def spotlight(self) -> dict:
        return self.button("spotlight")

    def open_url(self, url: str) -> dict:
        """Open a URL through Search."""
        return self._action("open_url", 2 + 0.1 * len(url), url=url)

    def wake(self) -> dict:
        """Wake a sleeping phone (USB remote wakeup, then a key)."""
        return self._action("wake", 3)

    def release_all(self) -> dict:
        """Release every key and button."""
        return self._action("release_all")


def _token_for(token: str | Mapping[str, str] | None, base_url: str) -> str | None:
    if token is None:
        return os.environ.get("IHC_TOKEN") or None
    if isinstance(token, str):
        return token or None
    return {u.rstrip("/"): t for u, t in token.items()}.get(base_url.rstrip("/")) or None


def _answers(base_url: str, timeout: float = 2.0) -> bool:
    try:
        return httpx.get(f"{base_url.rstrip('/')}/api/health", timeout=timeout).status_code == 200
    except httpx.HTTPError:
        return False


class Farm:
    """The phones of one or more boxes: Farm("http://box-a:8000", "http://box-b:8000"), or every
    box on the local network: Farm.discover().

    `token`: the boxes' API token, or {base URL: token} when they differ; by default $IHC_TOKEN
    (each box keeps its token in /var/lib/ihc/token)."""

    def __init__(self, *base_urls: str, timeout: float = 30, token: str | Mapping[str, str] | None = None):
        if not base_urls:
            raise ValueError("Farm needs at least one base URL, e.g. Farm('http://box.local:8000')")
        self.boxes = [Box(u, timeout, _token_for(token, u)) for u in base_urls]

    @classmethod
    def discover(cls, wait: float = 2.0, timeout: float = 30, token: str | Mapping[str, str] | None = None) -> Farm:
        """Every box announcing itself on the LAN (mDNS _ihc._tcp; needs the `zeroconf` package).
        IhcError when none answers within `wait` seconds."""
        from .discovery import discover

        urls = [u for u in discover(wait) if _answers(u)]  # an announcement can outlive its box
        if not urls:
            raise IhcError(f"no box answered on the local network within {wait:.0f} s")
        return cls(*urls, timeout=timeout, token=token)

    @property
    def urls(self) -> list[str]:
        return [b.base_url for b in self.boxes]

    def devices(self) -> list[Phone]:
        """Every phone, in box order."""
        return [p for b in self.boxes for p in b.phones()]

    def device(self, device_id: str) -> Phone:
        """The phone with this id (IhcError 404 if no box has it)."""
        for b in self.boxes:
            try:
                return Phone(b, b.json("GET", f"/api/devices/{device_id}"))
            except IhcError as e:
                if e.status_code != 404:
                    raise
        raise IhcError(f"no phone {device_id!r} on {', '.join(self.urls)}", 404, {"code": "no_device"})

    def close(self) -> None:
        for b in self.boxes:
            b.close()

    def __enter__(self) -> Farm:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
