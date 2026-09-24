"""Python SDK and CLI against live uvicorn servers (simulated phones, no serial timing)."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import threading
import time

import httpx
import pytest
import uvicorn

from ihc import cli
from ihc.api import create_app
from ihc.client import Farm, IhcError, RemoteDevice
from ihc.registry import simulated


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LiveServer:
    def __init__(self, count: int, **options):
        self.registry = simulated(count, simulate_timing=False, **options)
        for d in self.registry.devices():
            # report pacing is the pointer model's business (test_pointer); a busy test process
            # must not make these API tests flaky
            d.pointer.timing_tolerance = 0.02
        self.app = create_app(self.registry)
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        config = uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="warning",
                                ws_per_message_deflate=False, timeout_graceful_shutdown=1)
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started:
            assert time.monotonic() < deadline, "server did not start"
            time.sleep(0.01)

    def close(self) -> None:
        self.server.should_exit = True
        self.thread.join(10)
        self.registry.close()


@pytest.fixture(scope="module")
def hosts():
    # host A's phones follow absolute pointer reports: quick moves keep this SDK test short
    # (relative pointer moves through the API are covered by test_api)
    a, b = LiveServer(2, absolute=True), LiveServer(1)
    yield a, b
    a.close()
    b.close()


@pytest.fixture
def farm(hosts):
    with Farm(hosts[0].url, hosts[1].url, timeout=30) as f:
        yield f


def sim(host: LiveServer, device_id: str, op: str) -> None:
    httpx.post(f"{host.url}/api/devices/{device_id}/sim/{op}").raise_for_status()


def test_devices_across_hosts(hosts, farm):
    a, b = hosts
    phones = farm.devices()
    assert [(d.id, d.host) for d in phones] == [("sim-01", a.url), ("sim-02", a.url), ("sim-01", b.url)]
    assert all(isinstance(d, RemoteDevice) and d.model == "iPhone 15" and d.kind == "sim" for d in phones)
    d = farm.device("sim-02")
    assert d.host == a.url and d.state == "ready" and d.status()["id"] == "sim-02"
    with pytest.raises(IhcError) as e:
        farm.device("nope")
    assert e.value.status_code == 404
    with pytest.raises(IhcError) as e:
        Farm(f"http://127.0.0.1:{free_port()}", timeout=2).devices()
    assert e.value.status_code is None


def test_screenshot(farm, tmp_path):
    d = farm.device("sim-01")
    png = d.screenshot(tmp_path / "shot.png")
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and (tmp_path / "shot.png").read_bytes() == png
    assert d.screenshot(crop=False, format="jpeg")[:2] == b"\xff\xd8"


def test_actions(hosts, farm):
    a, _ = hosts
    d = farm.device("sim-01")
    phone = a.registry.extra("sim-01").phone
    sim(a, "sim-01", "open_app?name=Targets")
    x, y = phone.target_center_norm(6)
    assert d.tap(x, y)["action"] == "tap" and phone.tap_log[-1]["hit"]
    x, y = phone.target_center_norm(9)
    assert d.long_press(x, y)["action"] == "long_press" and phone.tap_log[-1]["long"]
    assert d.move(0.5, 0.5)["action"] == "move"
    assert d.swipe(0.5, 0.7, 0.5, 0.45)["action"] == "swipe" and phone.tap_log[-1]["kind"] == "swipe"
    sim(a, "sim-01", "open_app?name=Settings")
    d.scroll(0.5, 0.5, -2)
    assert phone.scroll["Settings"] > 0
    d.home()
    assert phone.screen == "home"
    d.key("cmd+space")
    d.type("Notes")
    assert phone.overlay == "spotlight" and phone.query == "Notes"
    d.key("esc")
    d.app_switcher()
    assert phone.overlay == "switcher"
    d.home()
    volume = phone.volume
    d.media("volume_up")
    assert phone.volume > volume
    d.open_url("http://a.b")
    assert phone.url == "http://a.b"
    assert d.release_all() == {"released": True}


def test_errors(hosts, farm):
    a, _ = hosts
    d = farm.device("sim-02")
    with pytest.raises(IhcError) as e:
        d.tap(1.5, 0.5)
    assert e.value.status_code == 422 and "off the screen" in e.value.message
    assert e.value.payload["code"] == "invalid_input" and "HTTP 422" in str(e.value)
    with pytest.raises(IhcError) as e:
        d.type("naïve")
    assert e.value.status_code == 422
    sim(a, "sim-02", "lock")
    try:
        with pytest.raises(IhcError) as e:
            d.home()
        assert e.value.status_code == 409 and e.value.payload["code"] == "device_error"
    finally:
        sim(a, "sim-02", "unlock")


def test_run_script(hosts, farm):
    a, _ = hosts
    d = farm.device("sim-02")
    phone = a.registry.extra("sim-02").phone
    out = d.run([{"type": "home"}, {"type": "wait", "seconds": 0.05}, {"type": "key", "combo": "cmd+space"},
                 {"type": "type", "text": "cal"}, {"type": "key", "combo": "esc"}])
    assert out["completed"] == out["total"] == 5 and phone.overlay is None
    with pytest.raises(IhcError) as e:
        d.run([{"type": "home"}, {"type": "tap", "x": 900, "y": 1, "space": "pt"}, {"type": "home"}])
    assert "step 1 (tap)" in e.value.message and [s["ok"] for s in e.value.payload["steps"]] == [True, False]
    with pytest.raises(IhcError) as e:
        d.run([{"type": "fly"}])
    assert e.value.status_code == 422


def test_mjpeg_viewer_disconnect_is_cleaned_up(hosts):
    a, _ = hosts
    hub = a.app.state.farm.hubs.get(a.registry.get("sim-01"))
    with httpx.Client(timeout=10) as http:
        for crop in ("false", "true"):
            with http.stream("GET", f"{a.url}/api/devices/sim-01/mjpeg?crop={crop}&fps=10") as r:
                assert r.status_code == 200
                got = b""
                for chunk in r.iter_bytes():
                    got += chunk
                    if got.count(b"Content-Length") >= 2:
                        break
                assert hub.watchers == 1
    deadline = time.monotonic() + 5
    while hub.watchers:
        assert time.monotonic() < deadline, "the stream kept its viewer after the client left"
        time.sleep(0.01)


def test_cli_devices(hosts, capsys):
    a, b = hosts
    assert cli.main(["devices", "--url", a.url, "--url", b.url]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines[0].split()[:3] == ["ID", "MODEL", "STATE"]
    assert len(lines) == 4 and all("ready" in ln and "sim" in ln for ln in lines[1:])
    assert cli.main(["devices", "--url", f"http://127.0.0.1:{free_port()}", "--timeout", "2"]) == 1


def test_cli_discover_prints_valid_toml(capsys, tmp_path):
    import tomllib

    assert cli.main(["discover", "--serial-dir", str(tmp_path), "--video-dir", str(tmp_path)]) == 1
    out = capsys.readouterr()
    assert tomllib.loads(out.out) == {} and "no rig found" in out.err


def test_cli_serve_starts_and_shuts_down_cleanly():
    """`ihc serve --sim` serves the API, and Ctrl+C stops it even with a live stream open."""
    port = free_port()
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    proc = subprocess.Popen([sys.executable, "-m", "ihc.cli", "serve", "--sim", "1", "--port", str(port),
                             "--host", "127.0.0.1", "--no-monitor", "--log-level", "warning"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=env)
    try:
        url = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 20
        while True:
            try:
                if httpx.get(f"{url}/api/health", timeout=1).json()["devices"] == 1:
                    break
            except httpx.HTTPError:
                pass
            assert time.monotonic() < deadline and proc.poll() is None, proc.stderr.read().decode()
            time.sleep(0.05)
        with httpx.Client(timeout=10) as http, http.stream("GET", f"{url}/api/devices/sim-01/mjpeg") as r:
            next(r.iter_bytes())
            proc.send_signal(signal.SIGINT)
            assert proc.wait(timeout=15) == 0
        err = proc.stderr.read().decode()
        assert "console at http://" in err
    finally:
        if proc.poll() is None:
            proc.kill()
