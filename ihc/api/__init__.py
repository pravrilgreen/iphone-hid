"""HTTP/WebSocket API and web console (see ihc.api.server)."""

from .server import create_app

__all__ = ["create_app"]
