"""Find control boxes on the LAN without configuration: mDNS / DNS-SD service `_ihc._tcp`.

A box announces itself when `ihc serve` starts; clients (the SDK's `Farm.discover()`, test runners)
browse for the service and get base URLs. Needs the optional `zeroconf` package (`pip install
iphone-hid[box]`); without it announcing is skipped and discovery returns nothing.
"""

from __future__ import annotations

import socket
import threading
import time

SERVICE = "_ihc._tcp.local."


def primary_ip() -> str:
    """This host's LAN address (the interface the default route uses); no packet is sent."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


class Announcement:
    def __init__(self, zc=None, info=None):
        self._zc, self._info = zc, info

    @property
    def active(self) -> bool:
        return self._zc is not None

    def close(self) -> None:
        if self._zc is not None:
            try:
                self._zc.unregister_service(self._info)
            finally:
                self._zc.close()
            self._zc = None


def advertise(port: int, *, name: str | None = None, devices: int = 0, version: str = "", address: str | None = None,
              auth: bool = False, log=None) -> Announcement:
    """Announce `http://<address>:<port>` as an ihc box. Returns a handle; close() withdraws it."""
    try:
        from zeroconf import ServiceInfo, Zeroconf
    except ImportError:
        if log:
            log("mdns_unavailable", hint="pip install zeroconf")
        return Announcement()
    ip = address or primary_ip()
    host = name or socket.gethostname()
    info = ServiceInfo(
        SERVICE,
        f"{host}.{SERVICE}",
        addresses=[socket.inet_aton(ip)],
        port=port,
        properties={"api": "/api", "devices": str(devices), "version": version, "auth": "token" if auth else "none"},
        server=f"{host}.local.",
    )
    zc = Zeroconf()
    zc.register_service(info, allow_name_change=True)
    if log:
        log("mdns_announced", name=info.name, url=f"http://{ip}:{port}")
    return Announcement(zc, info)


def discover(timeout: float = 2.0) -> list[str]:
    """Base URLs of the boxes answering on the LAN within `timeout` seconds."""
    try:
        from zeroconf import ServiceBrowser, ServiceListener, Zeroconf
    except ImportError:
        return []
    found: dict[str, str] = {}
    lock = threading.Lock()

    class Listener(ServiceListener):
        def add_service(self, zc, type_, name):
            info = zc.get_service_info(type_, name, timeout=int(timeout * 1000))
            if info and info.addresses and info.port:
                ip = socket.inet_ntoa(info.addresses[0])
                with lock:
                    found[name] = f"http://{ip}:{info.port}"

        def update_service(self, zc, type_, name):
            self.add_service(zc, type_, name)

        def remove_service(self, zc, type_, name):
            with lock:
                found.pop(name, None)

    zc = Zeroconf()
    try:
        ServiceBrowser(zc, SERVICE, Listener())
        time.sleep(timeout)
    finally:
        zc.close()
    with lock:
        return sorted(found.values())
