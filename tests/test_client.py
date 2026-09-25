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
from ihc.client import STEP_S, TYPE_S_PER_CHAR, Farm, IhcError, RemoteDevice
from ihc.registry import simulated

TOKEN = "sdk-test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


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
        self.events: list[tuple[str, dict]] = []
        self.app = create_app(self.registry, token=TOKEN, log=lambda event, **fields: self.events.append((event, fields)))
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
    with Farm(hosts[0].url, hosts[1].url, timeout=30, token=TOKEN) as f:
        yield f


def sim(host: LiveServer, device_id: str, op: str) -> None:
    httpx.post(f"{host.url}/api/devices/{device_id}/sim/{op}", headers=AUTH, json={}).raise_for_status()


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
    with httpx.Client(timeout=10, headers=AUTH) as http:
        for crop in ("false", "true"):
            with http.stream("GET", f"{a.url}/api/devices/sim-01/mjpeg?crop={crop}&fps=10") as r:
                assert r.status_code == 200
                got = b""
                for chunk in r.iter_bytes():
                    got += chunk
                    if got.count(b"Content-Length") >= 2:
                        break
                assert hub.watchers == 1
            # the server notices the disconnect on its next frame, a moment after the client left
            deadline = time.monotonic() + 5
            while hub.watchers:
                assert time.monotonic() < deadline, "the stream kept its viewer after the client left"
                time.sleep(0.01)


def test_cli_devices(hosts, capsys, tmp_path, monkeypatch):
    a, b = hosts
    monkeypatch.delenv("IHC_TOKEN", raising=False)
    assert cli.main(["devices", "--url", a.url, "--url", b.url, "--token", TOKEN]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines[0].split()[:3] == ["ID", "MODEL", "STATE"]
    assert len(lines) == 4 and all("ready" in ln and "sim" in ln for ln in lines[1:])
    assert cli.main(["devices", "--url", f"http://127.0.0.1:{free_port()}", "--timeout", "2"]) == 1
    assert cli.main(["devices", "--url", a.url]) == 1  # no token
    assert "IHC_TOKEN" in capsys.readouterr().err
    (tmp_path / "token").write_text(TOKEN + "\n")
    assert cli.main(["devices", "--url", a.url, "--token-file", str(tmp_path / "token")]) == 0
    monkeypatch.setenv("IHC_TOKEN", TOKEN)
    assert cli.main(["devices", "--url", a.url]) == 0
    capsys.readouterr()
    with pytest.raises(SystemExit, match="cannot read the token file"):
        cli.main(["devices", "--url", a.url, "--token-file", str(tmp_path / "missing")])
    (tmp_path / "empty").write_text("\n")
    with pytest.raises(SystemExit, match="is empty"):
        cli.main(["devices", "--url", a.url, "--token-file", str(tmp_path / "empty")])


def test_cli_discover_prints_valid_toml(capsys, tmp_path):
    try:
        import tomllib
    except ModuleNotFoundError:  # Python 3.10
        import tomli as tomllib

    assert cli.main(["discover", "--serial-dir", str(tmp_path), "--video-dir", str(tmp_path)]) == 1
    out = capsys.readouterr()
    assert tomllib.loads(out.out) == {} and "no rig found" in out.err


def serve(*args: str, env: dict | None = None) -> tuple[subprocess.Popen, str]:
    """`ihc serve --sim 1 ...` on a free port, once it answers; (process, base URL)."""
    port = free_port()
    env = {**{k: v for k, v in os.environ.items() if k != "IHC_TOKEN"}, "PYTHONUNBUFFERED": "1", **(env or {})}
    proc = subprocess.Popen([sys.executable, "-m", "ihc.cli", "serve", "--sim", "1", "--port", str(port),
                             "--host", "127.0.0.1", "--no-monitor", "--log-level", "warning", *args],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=env)
    url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 20
    while True:
        try:
            if httpx.get(f"{url}/api/health", timeout=1).json()["devices"] == 1:
                return proc, url
        except httpx.HTTPError:
            pass
        if time.monotonic() > deadline or proc.poll() is not None:
            proc.kill()
            raise AssertionError(proc.stderr.read().decode())
        time.sleep(0.05)


def test_cli_serve_starts_and_shuts_down_cleanly(tmp_path):
    """`ihc serve --sim` serves the API with its token, and Ctrl+C stops it even with a live stream open."""
    (tmp_path / "token").write_text(TOKEN + "\n")
    proc, url = serve("--token-file", str(tmp_path / "token"))
    try:
        assert httpx.get(f"{url}/api/devices").status_code == 401
        assert httpx.get(f"{url}/api/devices", headers=AUTH).status_code == 200
        with httpx.Client(timeout=10) as http, http.stream("GET", f"{url}/api/devices/sim-01/mjpeg?token={TOKEN}") as r:
            assert r.status_code == 200
            next(r.iter_bytes())
            proc.send_signal(signal.SIGINT)
            assert proc.wait(timeout=15) == 0
        err = proc.stderr.read().decode()
        assert "console at http://" in err and "API token required" in err and "WARNING" not in err
        assert TOKEN not in err  # the access log hides ?token=
    finally:
        if proc.poll() is None:
            proc.kill()


def test_access_log_hides_the_token():
    import logging

    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                               ("1.2.3.4:5", "GET", f"/api/devices/x/mjpeg?crop=true&token={TOKEN}&fps=5", "1.1", 200),
                               None)
    assert cli._RedactToken().filter(record)
    assert TOKEN not in record.getMessage() and "crop=true&token=***&fps=5" in record.getMessage()
    record = logging.LogRecord("uvicorn.error", logging.INFO, "", 0, '%s - "WebSocket %s" [accepted]',
                               ("1.2.3.4:5", f"/api/devices/x/stream?token={TOKEN}"), None)
    assert cli._RedactToken().filter(record) and TOKEN not in record.getMessage()


def test_cli_serve_warns_without_a_token():
    proc, url = serve()
    try:
        assert httpx.get(f"{url}/api/devices").status_code == 200
        proc.send_signal(signal.SIGINT)
        assert proc.wait(timeout=15) == 0
        err = proc.stderr.read().decode()
        assert "WARNING: no API token" in err and "anyone on this machine can control the phones" in err
    finally:
        if proc.poll() is None:
            proc.kill()
    proc, url = serve(env={"IHC_TOKEN": TOKEN})
    try:
        assert httpx.get(f"{url}/api/devices").status_code == 401
        assert httpx.get(f"{url}/api/devices", headers=AUTH).status_code == 200
    finally:
        proc.kill()
        proc.wait(5)


def test_farm_discover_finds_announced_boxes(hosts, monkeypatch):
    pytest.importorskip("zeroconf")
    from urllib.parse import urlparse

    from ihc import discovery

    url = urlparse(hosts[0].url)
    ann = discovery.advertise(url.port, name="ihc-sdk-test", devices=2, address=url.hostname)
    try:
        if not ann.active:
            pytest.skip("zeroconf could not start")
        try:
            farm = Farm.discover(wait=1.5, timeout=30, token=TOKEN)
        except IhcError:
            pytest.skip("no multicast on this network")
        with farm:
            assert hosts[0].url in farm.urls  # (other boxes on this network may answer too)
            ours = next(h for h in farm.hosts if h.base_url == hosts[0].url)
            assert {d.id for d in ours.devices()} >= {"sim-01", "sim-02"}
    finally:
        ann.close()
    monkeypatch.setattr(discovery, "discover", lambda wait: [])
    with pytest.raises(IhcError, match="no ihc box"):
        Farm.discover(wait=0.1)
    # a stale announcement (the box is gone) is left out
    monkeypatch.setattr(discovery, "discover", lambda wait: [f"http://127.0.0.1:{free_port()}", hosts[1].url])
    with Farm.discover(wait=0.1) as farm:
        assert farm.urls == [hosts[1].url]


# -- token, retries, timeouts --------------------------------------------------------------------------


def test_token(hosts, monkeypatch):
    a, b = hosts
    monkeypatch.delenv("IHC_TOKEN", raising=False)
    with Farm(a.url) as farm, pytest.raises(IhcError) as e:
        farm.devices()
    assert e.value.status_code == 401 and e.value.code == "unauthorized" and "IHC_TOKEN" in e.value.message
    with Farm(a.url, token="wrong") as farm, pytest.raises(IhcError) as e:
        farm.device("sim-01")
    assert e.value.status_code == 401
    monkeypatch.setenv("IHC_TOKEN", TOKEN)
    with Farm(a.url) as farm:
        assert len(farm.devices()) == 2
    monkeypatch.delenv("IHC_TOKEN")
    with Farm(a.url, b.url, token={a.url: TOKEN, b.url + "/": TOKEN}) as farm:  # one token per host
        assert len(farm.devices()) == 3


def test_actions_carry_idempotency_keys_and_scaled_timeouts(farm, monkeypatch):
    host = farm.hosts[0]
    d = farm.device("sim-01")
    sent = []
    real = host.http.request

    def spy(method, path, **kwargs):
        sent.append((path, (kwargs.get("headers") or {}).get("Idempotency-Key"), kwargs.get("timeout")))
        return real(method, path, **kwargs)

    monkeypatch.setattr(host.http, "request", spy)
    d.move(0.5, 0.5)
    d.move(0.5, 0.5)
    d.type("x" * 50)
    d.run([{"type": "wait", "seconds": 2}, {"type": "type", "text": "ab"}, {"type": "home"}])
    d.key("esc", idempotency_key="mine")
    (_, k1, t1), (_, k2, _), (_, _, t_type), (_, _, t_run), (_, k_mine, _) = sent
    assert k1 and k2 and k1 != k2 and k_mine == "mine"  # a fresh key per call unless given
    assert t1 == 30 + STEP_S
    assert t_type == 30 + STEP_S + 50 * TYPE_S_PER_CHAR  # proportional to the text
    assert t_run == 30 + 2 + (STEP_S + 2 * TYPE_S_PER_CHAR) + STEP_S  # the waits plus each step's allowance


def test_retry_with_the_same_key_acts_once(hosts, farm):
    a, _ = hosts
    d = farm.device("sim-01")
    phone = a.registry.extra("sim-01").phone
    sim(a, "sim-01", "open_app?name=Targets")
    x, y = phone.target_center_norm(4)
    taps = len(phone.tap_log)
    first = d.tap(x, y, idempotency_key="retry-1")
    assert d.tap(x, y, idempotency_key="retry-1") == first
    assert len(phone.tap_log) == taps + 1
    with pytest.raises(IhcError) as e:
        d.tap(0.1, 0.1, idempotency_key="retry-1")  # another request under the same key
    assert e.value.status_code == 422 and e.value.idempotency_key == "retry-1"


def test_an_action_whose_client_gave_up_is_not_performed(hosts):
    """The client timed out while the phone was busy: the queued tap must not happen later (a retry
    would make the phone act twice)."""
    a, _ = hosts
    dev = a.registry.get("sim-02")
    busy = threading.Thread(target=lambda: Farm(a.url, token=TOKEN, timeout=10).device("sim-02").run(
        [{"type": "wait", "seconds": 1.2}]))
    busy.start()
    time.sleep(0.3)
    taps = dev.counters["taps"]
    with pytest.raises(httpx.TimeoutException):
        httpx.post(f"{a.url}/api/devices/sim-02/tap", json={"x": 0.5, "y": 0.5}, headers=AUTH, timeout=0.3)
    busy.join()
    time.sleep(0.5)
    assert dev.counters["taps"] == taps
    skipped = [f for e, f in a.events if e == "action_skipped_client_gone"]
    assert skipped and skipped[-1]["device"] == "sim-02" and skipped[-1]["action"] == "tap"
