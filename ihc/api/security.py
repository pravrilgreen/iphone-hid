"""Access control in front of every route (HTTP and WebSocket): Host, Origin, token, request shape.

- Host: only IP literals, localhost, names ending in .local, this machine's name, the public URL's
  host and names allowed by the operator. A DNS-rebinding page reaches the box under the attacker's
  own name, which is refused (400) before anything else happens.
- Origin: a browser sends it on WebSockets and POSTs. One that is not this server (same host and
  port as the Host header), not the public URL and not allowed by the operator is refused (403);
  no Origin at all means no browser (SDK, curl), which passes.
- Token: with a token configured, /api/* needs `Authorization: Bearer <token>` (401 otherwise),
  except GET /api/health and the calibration page's events, which carry a per-device key instead
  (checked by the endpoint). WebSockets and the media URLs an <img> loads (MJPEG, screenshot) may
  pass it as `?token=` instead. Compared in constant time.
- POST: the body must be declared `Content-Type: application/json` (415 otherwise), even when
  empty: a cross-site form or "simple" request cannot declare it without a CORS preflight, which
  this server never grants. Bodies are read up front, at most `max_body` bytes (413).

A refused WebSocket is closed before the handshake completes (the client sees HTTP 403), except a
missing or wrong token: that one is accepted and closed with code 4401, which a browser can read
(it cannot read a refused handshake's status) to ask the operator for the token.
"""

from __future__ import annotations

import hmac
import ipaddress
import json
import re
import socket
from typing import Iterable
from urllib.parse import parse_qs, urlsplit

from starlette.datastructures import Headers

from . import models

MAX_BODY = 1 << 20
_NO_TOKEN = {("GET", "/api/health"), ("HEAD", "/api/health")}
_EVENTS = re.compile(r"/api/devices/[^/]+/calibration/events")
_MEDIA = re.compile(r"/api/devices/[^/]+/(mjpeg|screenshot)")
_SAFE = ("GET", "HEAD", "OPTIONS")
_DEFAULT_PORT = {"http": 80, "ws": 80, "https": 443, "wss": 443}
_GONE = object()


def split_host(value: str) -> tuple[str, int | None]:
    """("host", port) of a Host header or netloc; IPv6 without brackets, host lower-cased."""
    value = value.strip()
    if value.startswith("["):
        host, _, rest = value[1:].partition("]")
        port = rest[1:] if rest.startswith(":") else ""
    else:
        host, _, port = value.partition(":")
    host = host.lower().rstrip(".")
    return host, int(port) if port.isdigit() else None


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.split("%", 1)[0])
        return True
    except ValueError:
        return False


def _origin(value: str) -> tuple[str, str, int] | None:
    """(scheme, host, port) of an Origin header or URL; None if it has no host ("null")."""
    try:
        u = urlsplit(value.strip())
        host, port = split_host(u.netloc)
    except ValueError:
        return None
    if not u.scheme or not host:
        return None
    scheme = u.scheme.lower()
    return scheme, host, port if port is not None else _DEFAULT_PORT.get(scheme, 0)


class Policy:
    """What the Guard accepts. `token` None: no authentication (the server warns at start)."""

    def __init__(self, *, token: str | None = None, public_url: str | None = None,
                 allowed_hosts: Iterable[str] = (), allow_origins: Iterable[str] = (), max_body: int = MAX_BODY):
        self.token = token.encode() if token else None
        self.max_body = max_body
        hosts = [h.strip().lower().rstrip(".") for h in allowed_hosts if h.strip()]
        self.any_host = "*" in hosts
        names = {"localhost", *hosts}
        try:
            me = socket.gethostname().lower().rstrip(".")
            names |= {me, me.split(".")[0]}
        except OSError:
            pass
        public = _origin(public_url) if public_url else None
        if public is not None:
            names.add(public[1])
        self.hosts = names
        origins = [o.strip() for o in allow_origins if o.strip()]
        self.any_origin = "*" in origins
        self.origins = {o for o in (_origin(v) for v in origins) if o is not None}
        if public is not None:
            self.origins.add(public)

    def host_ok(self, value: str) -> bool:
        host, _ = split_host(value)
        return (self.any_host or host in self.hosts or host.endswith(".local") or host.endswith(".localhost")
                or _is_ip(host))

    def origin_ok(self, origin: str, host_header: str | None) -> bool:
        if self.any_origin:
            return True
        o = _origin(origin)
        if o is None:
            return False  # "null": a sandboxed frame or a file:// page
        if o in self.origins:
            return True
        if host_header is None:
            return False
        host, port = split_host(host_header)
        return o[1] == host and o[2] == (port if port is not None else _DEFAULT_PORT.get(o[0], 0))

    def token_ok(self, presented: str | None) -> bool:
        if self.token is None:
            return True
        return presented is not None and hmac.compare_digest(presented.encode("utf-8", "replace"), self.token)


def needs_token(kind: str, method: str | None, path: str) -> bool:
    if not (path == "/api" or path.startswith("/api/")):
        return False  # console, API docs, calibration page: nothing to protect
    if kind == "websocket":
        return True
    return (method, path) not in _NO_TOKEN and not (method == "POST" and _EVENTS.fullmatch(path))


def presented_token(scope, headers: Headers) -> str | None:
    """The token of the request: Authorization: Bearer, or ?token= where a browser cannot set headers."""
    scheme, _, value = headers.get("authorization", "").partition(" ")
    if scheme.lower() == "bearer" and value.strip():
        return value.strip()
    if scope["type"] == "websocket" or (scope["method"] in ("GET", "HEAD") and _MEDIA.fullmatch(scope["path"])):
        values = parse_qs(scope.get("query_string", b"").decode("latin-1")).get("token")
        if values:
            return values[-1]
    return None


class Guard:
    """ASGI middleware enforcing a Policy (see the module docstring); `log(event, **fields)`."""

    def __init__(self, app, policy: Policy, log=None):
        self.app = app
        self.policy = policy
        self.log = log or (lambda event, **fields: None)

    async def __call__(self, scope, receive, send) -> None:
        kind = scope["type"]
        if kind not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        headers = Headers(scope=scope)
        path, method = scope["path"], scope.get("method")
        host = headers.get("host")
        if host is not None and not self.policy.host_ok(host):
            self.log("request_refused", reason="host", host=host, path=path)
            return await self._refuse(scope, receive, send, 400,
                                      f"host {split_host(host)[0]!r} is not allowed (DNS rebinding protection): "
                                      "use an IP address or a .local name, or start the server with --allowed-host")
        origin = headers.get("origin")
        if origin is not None and (kind == "websocket" or method not in _SAFE) \
                and not self.policy.origin_ok(origin, host):
            self.log("request_refused", reason="origin", origin=origin, path=path)
            return await self._refuse(scope, receive, send, 403,
                                      f"origin {origin!r} is not allowed: start the server with --allow-origin")
        if needs_token(kind, method, path) and not self.policy.token_ok(presented_token(scope, headers)):
            return await self._refuse(scope, receive, send, 401, "this server needs its API token: "
                                      "Authorization: Bearer <token> (the box keeps it in /var/lib/ihc/token)")
        if kind == "http" and method not in _SAFE and path.startswith("/api/"):
            media = headers.get("content-type", "").split(";")[0].strip().lower()
            if media != "application/json":
                return await self._refuse(scope, receive, send, 415,
                                          "send POST requests with Content-Type: application/json (the body may be empty)")
            replay = await self._read_body(headers, receive)
            if replay is _GONE:
                return  # the client left while sending: nobody to answer
            if replay is None:
                return await self._refuse(scope, None, send, 413, f"request body over {self.policy.max_body} bytes")
            receive = replay
        await self.app(scope, receive, send)

    async def _read_body(self, headers: Headers, receive):
        """Read the whole body (at most max_body bytes). Returns a receive that replays it, then
        goes on with the connection's own messages (so the app still sees the client disconnect);
        None when the body is too large, _GONE when the client left meanwhile."""
        length = headers.get("content-length", "")
        if length.isdigit() and int(length) > self.policy.max_body:
            return None
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] != "http.request":
                return _GONE
            body += message.get("body", b"")
            if len(body) > self.policy.max_body:
                return None
            if not message.get("more_body", False):
                break
        pending = [{"type": "http.request", "body": bytes(body), "more_body": False}]

        async def replay():
            return pending.pop() if pending else await receive()

        return replay

    @staticmethod
    async def _refuse(scope, receive, send, status: int, message: str) -> None:
        if scope["type"] == "websocket":
            await receive()  # websocket.connect
            if status == 401:
                await send({"type": "websocket.accept"})
                await send({"type": "websocket.close", "code": 4401, "reason": message[:120]})
            else:
                await send({"type": "websocket.close", "code": 1008, "reason": message[:120]})
            return
        body = json.dumps({"error": message, "code": models.CODES.get(status, "internal")}).encode()
        head = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
        if status == 401:
            head.append((b"www-authenticate", b"Bearer"))
        if status == 413:
            head.append((b"connection", b"close"))
        await send({"type": "http.response.start", "status": status, "headers": head})
        await send({"type": "http.response.body", "body": body})
