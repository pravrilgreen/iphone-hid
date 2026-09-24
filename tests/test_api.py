"""HTTP/WebSocket API against simulated phones (real driver stack over a pty, no serial timing)."""

from __future__ import annotations

import re
import threading
import time
import warnings

import cv2
import numpy as np
import pytest

from ihc.api import create_app
from ihc.device import DeviceInfo, IPhoneDevice
from ihc.hid import protocol as p
from ihc.hid.fake import FakeBackend
from ihc.registry import simulated
from ihc.video.frame import Frame, encode_jpeg

with warnings.catch_warnings():  # "use httpx2" notice from starlette.testclient
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

MARK = b"ihc-passthrough-test"
TOKEN = "test-token-0123456789"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def make_app(reg, **options):
    """The app as `ihc serve` builds it: with a token, reached as http://testserver."""
    return create_app(reg, **{"public_url": "http://testserver", "token": TOKEN, **options})


def client(app, **kwargs) -> TestClient:
    """A TestClient that sends the token and declares its bodies JSON, as the SDK and the console do."""
    return TestClient(app, headers={**AUTH, "Content-Type": "application/json"}, **kwargs)


def relaxed(reg):
    """Report pacing is the pointer model's business (tests/test_pointer.py): a busy test process
    must not make API tests flaky."""
    for d in reg.devices():
        d.pointer.timing_tolerance = 0.02
    return reg


@pytest.fixture
def farm():
    reg = relaxed(simulated(2, simulate_timing=False))
    with client(make_app(reg)) as c:
        yield reg, c
    reg.close()


def wait_until(cond, timeout: float = 5.0, what: str = "condition") -> None:
    deadline = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        time.sleep(0.005)


def marked_jpeg(size=(640, 360), shade: int = 90) -> bytes:
    """A JPEG that no encoder would produce: a COM segment carries MARK, so any re-encode loses it."""
    data = encode_jpeg(np.full((size[1], size[0], 3), shade, np.uint8), 80)
    com = b"\xff\xfe" + (len(MARK) + 2).to_bytes(2, "big") + MARK
    return data[:2] + com + data[2:]


class PacedSource:
    """A capture card stand-in delivering the given JPEGs in turn at `fps`."""

    def __init__(self, jpegs: list[bytes], fps: float = 30.0):
        self.jpegs = jpegs
        self.fps = fps
        self.size = Frame.from_jpeg(jpegs[0]).size
        self._seq = -1
        self._next = 0.0
        self._lock = threading.Lock()

    def latest(self, newer_than: int = -1, timeout: float = 1.0) -> Frame:
        with self._lock:
            wait = self._next - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._next = time.monotonic() + 1.0 / self.fps
            self._seq += 1
            return Frame.from_jpeg(self.jpegs[self._seq % len(self.jpegs)], self._seq)

    def close(self) -> None:
        pass


class DeadSource:
    size = (1920, 1080)

    def latest(self, newer_than: int = -1, timeout: float = 1.0) -> Frame:
        raise TimeoutError("capture card unplugged")

    def close(self) -> None:
        pass


def recv_until(ws, pred, limit: int = 200):
    """WebSocket messages until pred(message) is true; returns them all."""
    got = []
    for _ in range(limit):
        m = ws.receive()
        got.append(m)
        if pred(m):
            return got
    raise AssertionError("expected message not received")


def recv_json_until(ws, pred, limit: int = 500) -> dict:
    for _ in range(limit):
        m = ws.receive_json()
        if pred(m):
            return m
    raise AssertionError("expected message not received")


def control_sync(ws, n: int) -> dict:
    ws.send_json({"t": "sync", "id": f"sync-{n}"})
    return recv_json_until(ws, lambda m: m.get("id") == f"sync-{n}")


def mjpeg_parts(body: bytes) -> list[bytes]:
    parts, i = [], 0
    while True:
        m = re.compile(rb"Content-Length: (\d+)\r\n\r\n").search(body, i)
        if not m:
            return parts
        n = int(m.group(1))
        parts.append(body[m.end():m.end() + n])
        i = m.end() + n


# -- devices, errors ------------------------------------------------------------------------------


def test_health_devices_and_status(farm):
    reg, c = farm
    h = c.get("/api/health").json()
    assert h["ok"] and h["devices"] == 2 and h["public_url"] == "http://testserver"
    devs = c.get("/api/devices").json()["devices"]
    assert [d["id"] for d in devs] == ["sim-01", "sim-02"]
    st = c.get("/api/devices/sim-02").json()
    assert st["state"] == "ready" and st["kind"] == "sim" and st["screen"]["points"] == [393, 852]
    assert st["pointer"]["mode"] in ("relative", "absolute")
    cal = c.get("/api/devices/sim-01/calibration").json()
    assert cal["calibrated"] and cal["method"] == "sim" and cal["mode"] == "relative"
    for path in ("/api/devices/nope", "/api/devices/nope/calibration", "/api/devices/nope/screenshot"):
        r = c.get(path)
        assert r.status_code == 404 and r.json() == {"error": "no device 'nope'", "code": "not_found"}
    r = c.post("/api/devices/nope/tap", json={"x": 0.5, "y": 0.5})
    assert r.status_code == 404 and r.json()["code"] == "not_found"
    assert c.get("/api/unknown").json()["code"] == "not_found"
    assert "/api/devices/{device_id}/tap" in c.get("/openapi.json").json()["paths"]


def test_screenshot_formats_and_errors(farm):
    reg, c = farm
    r = c.get("/api/devices/sim-01/screenshot")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    img = cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_COLOR)
    assert img.shape[:2] == (1080, 498)  # cropped to the phone
    r = c.get("/api/devices/sim-01/screenshot?format=png&crop=false")
    assert r.headers["content-type"] == "image/png" and r.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_COLOR).shape[:2] == (1080, 1920)
    assert c.get("/api/devices/sim-01/screenshot?format=gif").status_code == 422
    assert c.get("/api/devices/sim-01/screenshot?quality=5").status_code == 422
    reg.get("sim-02").source = DeadSource()
    r = c.get("/api/devices/sim-02/screenshot")
    assert r.status_code == 503 and r.json()["code"] == "no_video" and "unplugged" in r.json()["error"]


# -- actions ----------------------------------------------------------------------------------------


def test_every_action_endpoint(farm):
    reg, c = farm
    rig = reg.extra("sim-01")
    phone, chip = rig.phone, rig.chip

    def post(name, body=None):
        r = c.post(f"/api/devices/sim-01/{name}", json=body) if body is not None else c.post(f"/api/devices/sim-01/{name}")
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["ok"] is True
        return data["result"]

    phone.open_app("Targets")
    t = phone.target_center_norm(2)
    assert post("tap", {"x": t[0], "y": t[1]})["action"] == "tap"
    assert phone.tap_log[-1]["hit"] and not phone.tap_log[-1]["long"]
    t = phone.target_center_norm(10)
    assert post("long_press", {"x": t[0], "y": t[1]})["action"] == "long_press"
    assert phone.tap_log[-1]["hit"] and phone.tap_log[-1]["long"]
    assert post("move", {"x": 100, "y": 200, "space": "pt"})["x"] == 100
    assert post("swipe", {"x1": 0.5, "y1": 0.7, "x2": 0.5, "y2": 0.4})["action"] == "swipe"
    assert phone.tap_log[-1]["kind"] == "swipe"
    phone.open_app("Settings")
    post("scroll", {"x": 0.5, "y": 0.5, "amount": -2})
    assert phone.scroll["Settings"] > 0
    post("home")
    assert phone.screen == "home"
    post("key", {"combo": "cmd+space"})
    assert phone.overlay == "spotlight"
    post("type", {"text": "Notes"})
    assert phone.query == "Notes"
    post("key", {"combo": "esc"})
    post("app_switcher", {})
    assert phone.overlay == "switcher"
    volume = phone.volume
    post("media", {"key": "volume_up"})
    assert phone.volume > volume
    post("open_url", {"url": "http://x.io"})
    assert phone.screen == "Safari" and phone.url == "http://x.io"
    assert post("release_all") == {"released": True}
    assert chip.pointer.buttons == 0


@pytest.mark.parametrize("name, body, fragment", [
    ("tap", {"x": 0.5}, "y: Field required"),
    ("tap", None, "JSON request body is required"),
    ("tap", {"x": 0.5, "y": 0.5, "bogus": 1}, "bogus: Extra inputs"),
    ("tap", {"x": 0.5, "y": 0.5, "space": "inch"}, "space"),
    ("tap", {"x": 1.5, "y": 0.5}, "off the screen"),
    ("swipe", {"x1": 0.5, "y1": 0.5, "x2": 0.5, "y2": -0.1}, "y2"),
    ("scroll", {"x": 0.5, "y": 0.5, "amount": 1000}, "amount"),
    ("type", {"text": "héllo"}, "cannot type"),
    ("key", {"combo": "cmd+nope"}, "unknown key"),
    ("media", {"key": "louder"}, "unknown media key"),
    ("open_url", {"url": ""}, "url"),
    ("calibrate", {"options": {"bogus": 1}}, "unknown calibration options"),
    # calibration options are checked before the phone is touched (types and ranges)
    ("calibrate", {"options": {"validate": 2}}, "options.validate"),
    ("calibrate", {"options": {"validate": 101}}, "options.validate"),
    ("calibrate", {"options": {"validate": 8.0}}, "options.validate: Input should be a valid integer"),
    ("calibrate", {"options": {"max_error": 0.1}}, "options.max_error"),
    ("calibrate", {"options": {"max_error": "3"}}, "options.max_error"),
    ("calibrate", {"options": {"repeats": 6}}, "options.repeats"),
    ("calibrate", {"options": {"page_timeout": 0.5}}, "options.page_timeout"),
    ("calibrate", {"options": {"page_timeout": 121}}, "options.page_timeout"),
    ("calibrate", {"options": {"click_timeout": 0}}, "options.click_timeout"),
    ("calibrate", {"options": {"coarse_target": 50}}, "options.coarse_target"),
    ("calibrate", {"options": {"try_absolute": "false"}}, "options.try_absolute: Input should be a valid boolean"),
    ("calibrate", {"options": {"try_absolute": 0}}, "options.try_absolute"),
    ("calibrate", {"options": {"coarse_counts": [1]}}, "options.coarse_counts"),
    ("calibrate", {"options": {"fine_counts": [2, 400]}}, "options.fine_counts"),
    ("calibrate", {"options": "fast"}, "options"),
    ("calibrate", {"page_url": "file:///etc/passwd"}, "http:// or https://"),
    ("calibrate", {"page_url": "http://host/é"}, "cannot type"),
])
def test_invalid_input_is_422_and_sends_nothing(farm, name, body, fragment):
    reg, c = farm
    before = reg.get("sim-01").hid.stats["tx"]
    r = c.post(f"/api/devices/sim-01/{name}", json=body) if body is not None else c.post(f"/api/devices/sim-01/{name}")
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "invalid_input" and fragment in r.json()["error"]
    assert reg.get("sim-01").hid.stats["tx"] == before


def test_device_errors_are_409(farm):
    reg, c = farm
    assert c.post("/api/devices/sim-01/sim/lock").json()["result"]["state"] == "hid_disconnected"
    r = c.post("/api/devices/sim-01/tap", json={"x": 0.5, "y": 0.5})
    assert r.status_code == 409 and r.json()["code"] == "device_error" and "EXEC_ERROR" in r.json()["error"]
    r = c.post("/api/devices/sim-01/tap", json={"x": 900, "y": 10, "space": "pt"})
    assert r.status_code == 409 and "outside the screen" in r.json()["error"]


def test_hid_timeout_is_504():
    reg = simulated(1, simulate_timing=False, timeout=0.1)  # short reply timeout: a quick test
    try:
        with client(make_app(reg)) as c:
            chip = reg.extra("sim-01").chip
            chip.powered = False  # the chip stops answering
            try:
                r = c.post("/api/devices/sim-01/media", json={"key": "mute"})
                assert r.status_code == 504 and r.json()["code"] == "hid_timeout"
            finally:
                chip.powered = True
            assert c.post("/api/devices/sim-01/home").status_code == 200
    finally:
        reg.close()


def test_actions_script(farm):
    reg, c = farm
    phone = reg.extra("sim-01").phone
    phone.open_app("Settings")
    r = c.post("/api/devices/sim-01/actions", json={"actions": [
        {"type": "home"}, {"type": "wait", "seconds": 0.05}, {"type": "key", "combo": "cmd+space"},
        {"type": "type", "text": "set"}]}).json()
    assert r["ok"] and r["result"]["completed"] == r["result"]["total"] == 4
    assert [s["type"] for s in r["result"]["steps"]] == ["home", "wait", "key", "type"]
    assert phone.overlay == "spotlight" and phone.query == "set"

    r = c.post("/api/devices/sim-01/actions", json={"actions": [
        {"type": "key", "combo": "esc"}, {"type": "tap", "x": 900, "y": 1, "space": "pt"}, {"type": "home"}]}).json()
    assert not r["ok"] and r["error"].startswith("step 1 (tap)")
    assert [s["ok"] for s in r["result"]["steps"]] == [True, False]  # stopped at the error
    assert r["result"]["steps"][1]["status"] == 409

    r = c.post("/api/devices/sim-01/actions", json={"stop_on_error": False, "actions": [
        {"type": "tap", "x": 900, "y": 1, "space": "pt"}, {"type": "home"}]}).json()
    assert [s["ok"] for s in r["result"]["steps"]] == [False, True]

    tx = reg.get("sim-01").hid.stats["tx"]
    for bad, fragment in [({"actions": [{"type": "home"}, {"type": "jump"}]}, "actions[1] (jump): unknown action"),
                          ({"actions": [{"type": "tap", "x": 0.5}]}, "actions[0] (tap): y: Field required"),
                          ({"actions": [{"type": "wait", "seconds": -1}]}, "seconds"),
                          ({"actions": "home"}, "actions")]:
        r = c.post("/api/devices/sim-01/actions", json=bad)
        assert r.status_code == 422 and fragment in r.json()["error"], r.text
    assert reg.get("sim-01").hid.stats["tx"] == tx  # nothing ran


def test_end_to_end_tap_on_target(farm):
    reg, c = farm
    phone = reg.extra("sim-02").phone
    assert c.post("/api/devices/sim-02/sim/open_app?name=Targets").json()["ok"]
    assert phone.screen == "Targets"
    x, y = phone.target_center_norm(5)
    r = c.post("/api/devices/sim-02/tap", json={"x": x, "y": y})
    assert r.status_code == 200 and r.json()["result"]["plan"]["anchor"]
    assert phone.tap_log[-1]["target"] == 5 and phone.tap_log[-1]["hit"]
    assert c.get("/api/devices/sim-02").json()["last_result"]["action"] == "tap"


# -- live control -----------------------------------------------------------------------------------


def test_control_coalesces_moves_without_losing_any(farm):
    reg, c = farm
    dev, ptr = reg.get("sim-01"), reg.extra("sim-01").chip.pointer
    ptr.accel, ptr.gain = 0.0, 1.0  # 1 HID unit = 1 point, so movement adds up exactly
    with c.websocket_connect("/api/devices/sim-01/control") as ws:
        assert ws.receive_json()["t"] == "hello"
        x0, y0, r0 = ptr.x, ptr.y, dev.counters["live_reports"]
        for i in range(100):
            ws.send_json({"t": "mouse", "dx": 1, "dy": -1 if i % 2 else 0, "buttons": 0})
        control_sync(ws, 1)
        assert ptr.x - x0 == 100 and ptr.y - y0 == -50
        reports = dev.counters["live_reports"] - r0
        assert 1 <= reports < 50, reports  # coalesced

        x0, y0, r0 = ptr.x, ptr.y, dev.counters["live_reports"]
        ws.send_json({"t": "mouse", "dx": -150, "dy": 300, "buttons": 0})  # beyond one report's +-127
        control_sync(ws, 2)
        assert (ptr.x - x0, ptr.y - y0) == (-150, 300)
        assert dev.counters["live_reports"] - r0 == 3
        stats = recv_json_until(ws, lambda m: m["t"] == "stats")
        assert stats["dropped"] == 0 and stats["totals"]["coalesced"] > 50


def test_control_clicks_are_never_lost_or_reordered(farm):
    reg, c = farm
    chip = reg.extra("sim-01").chip
    chip.pointer.accel, chip.pointer.gain = 0.0, 1.0
    with c.websocket_connect("/api/devices/sim-01/control") as ws:
        seq0 = chip.event_seq
        x0 = chip.pointer.x
        for i in range(5):  # a burst of moves and clicks, all queued at once
            ws.send_json({"t": "mouse", "dx": 3, "dy": 0, "buttons": 0})
            ws.send_json({"t": "mouse", "dx": 2, "dy": 0, "buttons": p.MOUSE_LEFT})
            ws.send_json({"t": "mouse", "dx": 0, "dy": 0, "buttons": 0})
        ws.send_json({"t": "mouse", "dx": 0, "dy": 0, "buttons": p.MOUSE_RIGHT})  # Home
        ws.send_json({"t": "mouse", "dx": 0, "dy": 0, "buttons": 0})
        control_sync(ws, 1)
    events = [e for e in chip.events if e["seq"] > seq0 and e["event"] == "click"]
    assert [e["button"] for e in events] == ["left"] * 5 + ["right"]
    # each press landed after the movement sent before it: 5 points further every time
    assert [e["x"] for e in events[:5]] == [round(x0 + 5 * (i + 1), 1) for i in range(5)]
    assert reg.extra("sim-01").phone.screen == "home"


def test_control_keys_actions_and_errors(farm):
    reg, c = farm
    rig = reg.extra("sim-01")
    rig.phone.open_app("Notes")
    rig.phone.editing, rig.phone.editor_text = -1, ""
    kb = rig.chip.keyboard
    kb.text = ""
    with c.websocket_connect("/api/devices/sim-01/control") as ws:
        ws.receive_json()
        for mods, keys in [(2, [0x0B]), (2, []), (0, [0x08]), (0, []), (0, [0x0F]), (0, [0x0F, 0x12]), (0, [0x12]),
                           (0, [])]:
            ws.send_json({"t": "keys", "mods": mods, "keys": keys})
        control_sync(ws, 1)
        assert kb.text == "Helo" and kb.pressed == set()

        ws.send_json({"t": "home", "id": 7})
        r = recv_json_until(ws, lambda m: m.get("id") == 7)
        assert r["t"] == "result" and r["ok"] and r["result"]["action"] == "home" and r["action"] == "home"
        ws.send_json({"t": "tap", "id": 8, "x": 0.5})
        r = recv_json_until(ws, lambda m: m.get("id") == 8)
        assert not r["ok"] and "y: Field required" in r["error"]
        ws.send_json({"t": "tap", "id": 9, "x": 900, "y": 1, "space": "pt"})
        r = recv_json_until(ws, lambda m: m.get("id") == 9)
        assert not r["ok"] and "outside the screen" in r["error"]
        ws.send_json({"t": "teleport"})
        assert "unknown message type" in recv_json_until(ws, lambda m: m["t"] == "error")["error"]
        ws.send_text("{not json")
        assert "JSON objects" in recv_json_until(ws, lambda m: m["t"] == "error")["error"]
        ws.send_json({"t": "keys", "mods": 0, "keys": [1, 2, 3, 4, 5, 6, 7]})
        assert "at most 6" in recv_json_until(ws, lambda m: m["t"] == "error")["error"]
        ws.send_json({"t": "ping", "ts": 123})
        assert recv_json_until(ws, lambda m: m["t"] == "pong")["ts"] == 123


def test_control_releases_everything_on_close(farm):
    reg, c = farm
    chip = reg.extra("sim-01").chip
    with c.websocket_connect("/api/devices/sim-01/control") as ws:
        ws.send_json({"t": "mouse", "dx": 0, "dy": 0, "buttons": p.MOUSE_LEFT})
        ws.send_json({"t": "keys", "mods": 0x08, "keys": [0x04]})
        control_sync(ws, 1)
        assert chip.pointer.buttons == p.MOUSE_LEFT and chip.keyboard.pressed == {0x04}
    wait_until(lambda: chip.pointer.buttons == 0 and not chip.keyboard.pressed, what="release after close")
    with c.websocket_connect("/api/devices/sim-01/control") as ws:
        ws.send_json({"t": "mouse", "dx": 0, "dy": 0, "buttons": p.MOUSE_LEFT})
        control_sync(ws, 2)
        ws.send_json({"t": "release"})
        control_sync(ws, 3)
        assert chip.pointer.buttons == 0


def test_control_absolute_pointer():
    reg = relaxed(simulated(1, simulate_timing=False, absolute=True))
    try:
        dev, rig = reg.get("sim-01"), reg.extra("sim-01")
        with client(make_app(reg)) as c, c.websocket_connect("/api/devices/sim-01/control") as ws:
            assert ws.receive_json()["mode"] == "absolute"
            r0 = dev.counters["live_reports"]
            for i in range(1, 41):
                ws.send_json({"t": "abs", "x": 0.25 * i / 40, "y": 0.5, "buttons": 0})
            control_sync(ws, 1)
            assert dev.counters["live_reports"] - r0 < 40  # coalesced to the newest position
            wait_until(lambda: abs(rig.chip.pointer.current()[0] - 0.25 * 393) < 1, what="absolute glide")
            seq0 = rig.chip.event_seq
            ws.send_json({"t": "abs", "x": 0.25, "y": 0.5, "buttons": 1})
            ws.send_json({"t": "abs", "x": 0.25, "y": 0.5, "buttons": 0})
            control_sync(ws, 2)
            clicks = [e for e in rig.chip.events if e["seq"] > seq0 and e["event"] == "click"]
            assert len(clicks) == 1 and abs(clicks[0]["x"] - 98.25) < 1 and abs(clicks[0]["y"] - 426) < 1
    finally:
        reg.close()


def test_control_unknown_device(farm):
    reg, c = farm
    with c.websocket_connect("/api/devices/nope/control") as ws:
        assert ws.receive_json() == {"t": "error", "error": "no device 'nope'", "code": "not_found"}


# -- video ------------------------------------------------------------------------------------------


def test_stream_sends_jpeg_frames_and_status(farm):
    reg, c = farm
    with c.websocket_connect("/api/devices/sim-01/stream") as ws:
        msgs = recv_until(ws, lambda m: m.get("bytes") is not None)
        status = [m for m in msgs if m.get("text")]
        import json

        st = json.loads(status[0]["text"])
        assert st["type"] == "status" and st["id"] == "sim-01" and st["screen_rect"]["w"] == 498
        assert msgs[-1]["bytes"][:2] == b"\xff\xd8"
        assert Frame.from_jpeg(msgs[-1]["bytes"]).size == (1920, 1080)  # passthrough: the whole frame
        st = json.loads(recv_until(ws, lambda m: m.get("text") and '"frame":{' in m["text"])[-1]["text"])
        assert st["frame"]["width"] == 1920 and st["stream"]["passthrough"] and st["stream"]["sent"] >= 1

        ws.send_json({"crop": True, "width": 120})
        frame = recv_until(ws, lambda m: m.get("bytes") is not None and Frame.from_jpeg(m["bytes"]).width == 120)
        img = cv2.imdecode(np.frombuffer(frame[-1]["bytes"], np.uint8), cv2.IMREAD_COLOR)
        assert img.shape[1] == 120 and abs(img.shape[0] - 120 * 1080 / 498) <= 1
    with c.websocket_connect("/api/devices/nope/stream") as ws:
        assert ws.receive_json()["error"] == "no device 'nope'"


def test_stream_slow_client_never_stalls_others(farm):
    reg, c = farm
    reg.get("sim-01").source = PacedSource([marked_jpeg(shade=60), marked_jpeg(shade=200)], fps=30)
    with c.websocket_connect("/api/devices/sim-01/stream?ack=true") as stuck, \
            c.websocket_connect("/api/devices/sim-01/stream") as fast:
        recv_until(stuck, lambda m: m.get("bytes") is not None)  # its one frame in flight, never acked
        t0 = time.monotonic()
        frames = 0
        while time.monotonic() - t0 < 1.0:
            if fast.receive().get("bytes") is not None:
                frames += 1
        assert frames >= 15  # the stuck viewer holds nobody back
        more = recv_until(stuck, lambda m: m.get("text") is not None and time.monotonic() - t0 > 1.5)
        assert all(m.get("bytes") is None for m in more)  # still no second frame without an ack
        stuck.send_json({"t": "ack"})
        recv_until(stuck, lambda m: m.get("bytes") is not None)


def test_mjpeg_forwards_the_capture_jpeg_untouched(farm):
    reg, c = farm
    reg.get("sim-02").source = DeadSource()
    r = c.get("/api/devices/sim-02/mjpeg")
    assert r.status_code == 503 and r.json()["code"] == "no_video"
    reg.get("sim-02").source = reg.extra("sim-02").capture

    reg.get("sim-01").source = PacedSource([marked_jpeg()])
    r = c.get("/api/devices/sim-01/mjpeg?frames=2")
    assert r.status_code == 200 and r.headers["content-type"] == "multipart/x-mixed-replace; boundary=frame"
    parts = mjpeg_parts(r.content)
    assert len(parts) == 2 and parts[0] == marked_jpeg()  # byte for byte: no decode, no encode
    assert r.content.startswith(b"--frame\r\nContent-Type: image/jpeg\r\n")

    r = c.get("/api/devices/sim-02/mjpeg?frames=1&crop=true&width=100")
    (part,) = mjpeg_parts(r.content)
    assert MARK not in part
    img = cv2.imdecode(np.frombuffer(part, np.uint8), cv2.IMREAD_COLOR)
    assert img.shape[1] == 100 and abs(img.shape[0] - 100 * 1080 / 498) <= 1

    assert c.get("/api/devices/sim-01/mjpeg?width=2").status_code == 422
    assert c.get("/api/devices/nope/mjpeg").status_code == 404


# -- simulator, calibration page, console -----------------------------------------------------------


def test_sim_controls(farm):
    reg, c = farm
    rig, dev = reg.extra("sim-01"), reg.get("sim-01")

    def sim(op, expect=200):
        r = c.post(f"/api/devices/sim-01/sim/{op}")
        assert r.status_code == expect, r.text
        return r.json()

    assert sim("lock")["result"]["state"] == "hid_disconnected"
    dev.check()
    assert dev.state == "hid_disconnected" and c.get("/api/devices/sim-01").json()["state"] == "hid_disconnected"
    assert sim("unlock")["result"]["state"] == "ready"
    assert sim("prompt_accessory")["result"]["state"] == "hid_disconnected"
    assert sim("allow_accessory")["result"]["state"] == "ready"
    assert sim("signal_off")["result"]["state"] == "no_signal" and not rig.capture.signal
    assert sim("signal_on")["result"]["state"] == "ready"
    assert sim("open_app?name=Notes")["ok"] and rig.phone.screen == "Notes"
    assert sim("home")["ok"] and rig.phone.screen == "home"
    assert "name" in sim("open_app", 422)["error"]
    assert "no app" in sim("open_app?name=Doom", 422)["error"]
    assert sim("explode", 422)["code"] == "invalid_input"
    assert c.post("/api/devices/nope/sim/lock").status_code == 404

    hw = IPhoneDevice(DeviceInfo("hw-01", "iPhone 15", "hardware"), FakeBackend(), PacedSource([marked_jpeg()]))
    reg.add(hw)
    r = c.post("/api/devices/hw-01/sim/lock")
    assert r.status_code == 404 and "not a simulated device" in r.json()["error"]


def page_key(c, device_id: str = "sim-01") -> str:
    """The calibration page's current key, from the page_url GET .../calibration gives an operator."""
    url = c.get(f"/api/devices/{device_id}/calibration").json()["page_url"]
    assert url.startswith(f"http://testserver/calibrate/{device_id}?k=")
    return url.rpartition("?k=")[2]


def test_calibration_page_and_events(farm):
    reg, c = farm
    dev = reg.get("sim-01")
    k = page_key(c)
    phone = TestClient(c.app, headers={"Content-Type": "application/json"})  # Safari: no API token
    r = phone.get(f"/calibrate/sim-01?k={k}")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert "iphone-hid calibration" in r.text and "user-scalable=no" in r.text
    assert "/calibration/events" in r.text and r.headers["cache-control"].startswith("no-cache")
    assert c.get("/calibrate/nope").status_code == 404

    events = f"/api/devices/sim-01/calibration/events?k={k}"
    hello = {"type": "hello", "screen_w": 393, "screen_h": 852, "inner_w": 393, "inner_h": 659, "dpr": 3, "ua": "Safari"}
    assert phone.post(events, json=hello).json()["result"] == {"accepted": 1}
    assert dev.clicks.wait_page(0.1)["inner_h"] == 659
    batch = [{"type": "move", "x": 1, "y": 2}, {"type": "click", "x": 10.5, "y": 20, "button": 0},
             {"type": "click", "x": 11, "y": 21, "button": 0}]
    assert phone.post(events, json=batch).json()["result"] == {"accepted": 3}
    assert dev.clicks.moves == 1
    assert [dev.clicks.next_click(0.1)["x"] for _ in range(2)] == [10.5, 11]
    for bad in ({"type": "click", "x": 1}, {"type": "tap", "x": 1, "y": 1}, {"type": "hello", "screen_w": 1}, [1],
                {"type": "click", "x": 1, "y": 2, "button": "left"}, [{"type": "move", "x": 1, "y": 1}] * 501):
        r = phone.post(events, json=bad)
        assert r.status_code == 422 and r.json()["code"] == "invalid_input", bad
    r = phone.post(events, content=b'{"type": "click", "x": NaN, "y": 1}')
    assert r.status_code == 422
    assert dev.clicks.next_click(0.01) is None  # nothing of a rejected batch was pushed
    assert phone.post(f"/api/devices/nope/calibration/events?k={k}", json=hello).status_code == 404

    # only the known fields are kept (pid: the page load)
    ev = {"type": "click", "x": 5, "y": 6, "button": 0, "pid": "a1b2", "seq": 3, "t": 12.5, "evil": "x" * 100,
          "ua": "S" * 5000}
    assert phone.post(events, json=ev).status_code == 200
    got = dev.clicks.next_click(0.1)
    assert got == {"type": "click", "x": 5, "y": 6, "button": 0, "pid": "a1b2", "seq": 3, "t": 12.5, "ua": "S" * 512}


def test_calibration_key(farm):
    """The phone's page cannot carry the API token: its events need the page's key instead."""
    reg, c = farm
    dev = reg.get("sim-01")
    phone = TestClient(c.app, headers={"Content-Type": "application/json"})
    click = {"type": "click", "x": 1, "y": 2}
    k = page_key(c)
    assert page_key(c) == k  # stable until a calibration ends
    assert page_key(c, "sim-02") != k  # one per device
    for query in ("", "?k=", "?k=wrong", f"?k={page_key(c, 'sim-02')}"):
        r = phone.post(f"/api/devices/sim-01/calibration/events{query}", json=click)
        assert r.status_code == 403 and r.json()["code"] == "forbidden", query
        r = phone.get(f"/calibrate/sim-01{query}")
        assert r.status_code == 403 and "expired" in r.text, query
    # a wrong key is refused before the body is even looked at
    assert phone.post("/api/devices/sim-01/calibration/events?k=wrong", json={"bogus": 1}).status_code == 403
    assert dev.clicks.next_click(0.01) is None
    assert phone.post(f"/api/devices/sim-01/calibration/events?k={k}", json=click).status_code == 200
    assert dev.clicks.next_click(0.1)["x"] == 1
    # the token is no substitute for the key, and the key none for the token
    assert c.post("/api/devices/sim-01/calibration/events", json=click).status_code == 403
    assert phone.get(f"/api/devices/sim-01/calibration?k={k}").status_code == 401
    # the calibrate endpoint opens the page with the current key, and makes a new one once it ends
    r = c.post("/api/devices/sim-01/calibrate", json={"open_page": False, "options": {"page_timeout": 1}})
    assert r.status_code == 409 and "did not load" in r.json()["error"]
    k2 = page_key(c)
    assert k2 != k
    assert phone.post(f"/api/devices/sim-01/calibration/events?k={k}", json=click).status_code == 403
    assert phone.post(f"/api/devices/sim-01/calibration/events?k={k2}", json=click).status_code == 200
    # a page_url given by the caller gets the key too (a proxy path: the simulated Safari shows no page there)
    r = c.post("/api/devices/sim-01/calibrate", json={"page_url": "http://10.0.0.9:8000/ihc/cal/sim-01?x=1",
                                                      "options": {"page_timeout": 1}})
    assert r.status_code == 409
    assert reg.extra("sim-01").phone.url == f"http://10.0.0.9:8000/ihc/cal/sim-01?x=1&k={k2}"
    assert page_key(c) not in (k, k2)


def test_calibration_hints_at_an_expired_page(farm):
    """A calibration that fails while the page on the phone posts with an old key says so."""
    reg, c = farm
    phone = TestClient(c.app, headers={"Content-Type": "application/json"})
    old = page_key(c)
    c.post("/api/devices/sim-01/calibrate", json={"open_page": False, "options": {"page_timeout": 1}})
    stop = threading.Event()

    def stale_page():  # the page opened for the previous calibration keeps sending heartbeats
        while not stop.is_set():
            phone.post(f"/api/devices/sim-01/calibration/events?k={old}", json={
                "type": "hello", "screen_w": 393, "screen_h": 852, "inner_w": 393, "inner_h": 659})
            time.sleep(0.05)

    t = threading.Thread(target=stale_page)
    t.start()
    try:
        r = c.post("/api/devices/sim-01/calibrate", json={"open_page": False, "options": {"page_timeout": 1}})
    finally:
        stop.set()
        t.join()
    assert r.status_code == 409 and "expired key" in r.json()["error"], r.text


def test_console_is_served_without_caching(farm):
    reg, c = farm
    for path, kind in (("/", "text/html"), ("/app.js", "javascript"), ("/style.css", "text/css")):
        r = c.get(path)
        assert r.status_code == 200 and kind in r.headers["content-type"]
        assert r.headers["cache-control"] == "no-cache"
    assert "iphone-hid" in c.get("/").text


@pytest.mark.slow
def test_calibrate_through_safari_page():
    reg = relaxed(simulated(1, calibrated=False, simulate_timing=False))
    try:
        with client(make_app(reg)) as c:
            assert c.get("/api/devices/sim-01/calibration").json()["method"] == "guess"
            r = c.post("/api/devices/sim-01/calibrate", json={"options": {"repeats": 1, "validate": 3}})
            assert r.status_code == 200, r.text
            result = r.json()["result"]
            assert result["page_url"].startswith("http://testserver/calibrate/sim-01?k=")
            assert result["calibration"]["method"] == "safari"
            st = c.get("/api/devices/sim-01").json()
            assert st["calibration"]["calibrated"] and st["calibration"]["method"] == "safari"
            assert st["calibration"]["validation"]["mean"] < 3.0
            phone = reg.extra("sim-01").phone
            phone.open_app("Targets")
            x, y = phone.target_center_norm(13)
            c.post("/api/devices/sim-01/tap", json={"x": x, "y": y})
            assert phone.tap_log[-1]["hit"]
    finally:
        reg.close()


def test_calibrate_on_a_page_opened_by_hand():
    """open_page=false: Spotlight is not used; the page the operator opened is calibrated."""
    reg = relaxed(simulated(1, calibrated=False, simulate_timing=False, absolute=True))
    try:
        with client(make_app(reg)) as c:
            phone = reg.extra("sim-01").phone
            page_url = c.get("/api/devices/sim-01/calibration").json()["page_url"]
            phone.open_url(page_url)  # typed in Safari by hand
            time.sleep(0.5)
            spotlight = len(reg.extra("sim-01").chip.keyboard.shortcuts)
            r = c.post("/api/devices/sim-01/calibrate", json={"open_page": False, "options": {"validate": 3}})
            assert r.status_code == 200, r.text
            assert r.json()["result"]["calibration"]["method"] == "safari"
            assert len(reg.extra("sim-01").chip.keyboard.shortcuts) == spotlight  # no Cmd+Space sent
            r = c.post("/api/devices/sim-01/calibrate", json={"options": {"try_absolute": False, "bogus": 1}})
            assert r.status_code == 422
    finally:
        reg.close()
