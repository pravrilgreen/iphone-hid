"""The real calibration page (web/calibrate.html) in a headless browser against a live server:
the key in its link, page-load id, heartbeats, clicks, and what happens when the key rotates.
Skipped when Playwright or its Chromium is not installed."""

import os
import time

import httpx
import pytest

from test_client import LiveServer, TOKEN

playwright = pytest.importorskip("playwright.sync_api")

CHROMIUM = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"


@pytest.fixture(scope="module")
def browser():
    with playwright.sync_playwright() as p:
        kwargs = {"executable_path": CHROMIUM} if os.path.exists(CHROMIUM) else {}
        try:
            b = p.chromium.launch(**kwargs)
        except Exception as e:  # no browser in this environment
            pytest.skip(f"no Chromium: {e}")
        yield b
        b.close()


@pytest.fixture
def server():
    s = LiveServer(1)
    yield s
    s.close()


def page_url(server) -> str:
    r = httpx.get(f"{server.url}/api/devices/sim-01/calibration", headers={"Authorization": f"Bearer {TOKEN}"})
    r.raise_for_status()
    url = r.json()["page_url"]
    assert "?k=" in url
    return url.replace(url.split("/calibrate/")[0], server.url)  # the phone's view of the server


def test_page_sends_numbered_events_with_its_key(server, browser):
    dev = server.registry.get("sim-01")
    page = browser.new_page(viewport={"width": 393, "height": 659})
    page.goto(page_url(server))
    time.sleep(0.8)
    page.mouse.move(100, 200)
    page.mouse.move(130, 240)
    for x, y in ((50, 100), (200, 300)):
        page.mouse.click(x, y)
        time.sleep(0.1)
    time.sleep(0.5)
    events = dev.clicks.take()
    kinds = {k: sum(e["type"] == k for e in events) for k in ("hello", "move", "click")}
    assert kinds["hello"] >= 2 and kinds["click"] == 2  # heartbeats, and both clicks
    seqs = [e["seq"] for e in events]
    assert seqs == list(range(seqs[0], seqs[0] + len(seqs)))  # none lost
    assert len({e["pid"] for e in events}) == 1 and events[0]["heartbeat_ms"] == 250
    assert [(e["x"], e["y"]) for e in events if e["type"] == "click"] == [(50, 100), (200, 300)]
    page.close()


def test_expired_link_stops_the_page(server, browser):
    url = page_url(server)
    page = browser.new_page()
    assert page.goto(url.split("?k=")[0] + "?k=wrong-key-000000").status == 403  # a guessed link
    page.goto(url)
    time.sleep(0.5)
    server.app.state.farm.keys.rotate("sim-01")  # what the end of any calibration does
    deadline = time.monotonic() + 5
    while "expired" not in page.inner_text("#info") and time.monotonic() < deadline:
        time.sleep(0.1)
    assert "expired" in page.inner_text("#info")
    keys = server.app.state.farm.keys
    rejected = keys.rejected["sim-01"]
    time.sleep(0.8)
    assert keys.rejected["sim-01"] == rejected  # an expired page stops sending (no stream of 403s)
    page.close()
