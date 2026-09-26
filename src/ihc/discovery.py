"""Find boxes on the local network without configuration: each box announces the mDNS / DNS-SD
service `_ihc._tcp`. Needs the `zeroconf` package (pip install "iphone-hid[discovery]")."""

from __future__ import annotations

import socket
import threading
import time

SERVICE = "_ihc._tcp.local."


def discover(timeout: float = 2.0) -> list[str]:
    """Base URLs of the boxes answering on the LAN within `timeout` seconds (none without zeroconf)."""
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
                scheme = (info.properties or {}).get(b"scheme") or b"http"
                with lock:
                    found[name] = f"{scheme.decode()}://{ip}:{info.port}"

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
