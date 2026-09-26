"""The SDK and the `ihc` command against a box running a simulated iPhone."""

from __future__ import annotations

import subprocess
import sys

import httpx
import pytest

from ihc import Farm, IhcError, __version__
from ihc.cli import main as cli


@pytest.fixture
def phone(box):
    with Farm(box.url, token=box.token) as farm:
        p = farm.devices()[0]
        p.home()
        box.wait_sim(lambda s: s["screen"] == "home", "home first")
        yield p


def test_the_box_lists_its_phone(box):
    with Farm(box.url, token=box.token) as farm:
        phones = farm.devices()
        assert [p.id for p in phones] == ["iphone-sim"]
        assert phones[0].state == "ready"
        assert farm.device("iphone-sim").url == box.url
        with pytest.raises(IhcError) as e:
            farm.device("iphone-other")
        assert e.value.status_code == 404


def test_a_wrong_token_is_refused(box):
    with Farm(box.url, token="wrong") as farm, pytest.raises(IhcError) as e:
        farm.devices()
    assert e.value.status_code == 401 and e.value.code == "unauthorized"


def test_tap_type_and_home(box, phone):
    phone.tap(0.374, 0.142)  # the Notes icon
    box.wait_sim(lambda s: s.get("app") == "Notes", "Notes open")
    result = phone.type("Hi there")
    assert result["ok"] and result["action"] == "type"
    box.wait_sim(lambda s: s["notes"] == "Hi there", "typed")
    phone.home()
    box.wait_sim(lambda s: s["screen"] == "home", "home")


def test_untypeable_text_is_refused_before_anything_is_sent(box, phone):
    before = box.sim()["reports"]
    with pytest.raises(IhcError) as e:
        phone.type("naïve")
    assert e.value.status_code == 400 and "ï" in e.value.message
    assert box.sim()["reports"] == before


def test_swipe_drag_scroll_and_buttons(box, phone):
    phone.swipe(0.9, 0.5, 0.1, 0.5, duration_ms=200)
    box.wait_sim(lambda s: s["page"] == 1, "second home screen page")
    phone.swipe(0.1, 0.5, 0.9, 0.5, duration_ms=200)
    box.wait_sim(lambda s: s["page"] == 0, "first page again")
    phone.tap(0.14, 0.142)  # Settings
    box.wait_sim(lambda s: s.get("app") == "Settings", "Settings open")
    phone.drag(0.5, 0.8, 0.5, 0.4, hold_ms=100, duration_ms=300, rest_ms=150)
    st = box.wait_sim(lambda s: s["scroll"] > 250, "the list dragged")
    phone.scroll(0.5, 0.5, 3)  # towards the top
    box.wait_sim(lambda s: s["scroll"] < st["scroll"], "scrolled back")
    volume = box.sim()["volume"]
    phone.button("volume_up")
    box.wait_sim(lambda s: s["volume"] > volume, "louder")
    phone.app_switcher()
    box.wait_sim(lambda s: s["screen"] == "switcher", "app switcher")
    phone.key("cmd+space")
    box.wait_sim(lambda s: s["screen"] == "search", "search")
    assert phone.wake()["ok"]
    with pytest.raises(IhcError) as e:
        phone.button("power")
    assert e.value.status_code == 400


def test_screenshots(box, phone, tmp_path):
    png = phone.screenshot(tmp_path / "s.png", wait=True)
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and (tmp_path / "s.png").read_bytes() == png
    jpg = phone.screenshot(format="jpeg")
    assert jpg[:2] == b"\xff\xd8"


def test_status_reports_the_last_action_and_input_latency(box, phone):
    phone.tap(0.5, 0.95)
    st = phone.status()
    assert st["last_action"]["action"] == "tap" and st["last_action"]["ok"]
    assert st["usb"]["connected"] and st["video"]["state"] == "ok"
    assert {b["name"] for b in st["buttons"]} >= {"home", "app_switcher", "volume_up", "volume_down"}


def test_cli(box, capsys):
    base = ["--url", box.url, "--token", box.token]
    assert cli(base + ["devices"]) == 0
    assert "iphone-sim\tready" in capsys.readouterr().out
    assert cli(base + ["tap", "0.5", "0.5"]) == 0
    assert cli(base + ["type", "é"]) == 1
    assert "cannot type" in capsys.readouterr().err
    r = subprocess.run([sys.executable, "-m", "ihc.cli", "--version"], capture_output=True, text=True)
    assert __version__ in r.stdout


def test_errors_say_whether_to_retry(box, phone):
    httpx.post(f"{box.url}/api/devices/iphone-sim/sim", json={"usb": "asleep"},
               headers={"Authorization": f"Bearer {box.token}"})
    try:
        with pytest.raises(IhcError) as e:
            phone.tap(0.5, 0.5)
        assert e.value.status_code == 503 and e.value.code == "asleep" and e.value.retryable
        with pytest.raises(IhcError) as e:
            phone.wait_ready(timeout=0.3, interval=0.1)
        assert e.value.code == "asleep"
    finally:
        httpx.post(f"{box.url}/api/devices/iphone-sim/sim", json={"usb": "connected"},
                   headers={"Authorization": f"Bearer {box.token}"})
    assert phone.wait_ready(timeout=5)["state"] in ("ready", "busy")
    with pytest.raises(IhcError) as e:
        Farm("http://127.0.0.1:9", token="x").devices()
    assert e.value.code == "unreachable" and e.value.status_code is None and e.value.retryable
    with pytest.raises(IhcError) as e:
        phone.tap(0.5, 1.5)
    assert e.value.code == "bad_request" and not e.value.retryable


def test_media_keys_and_orientation(box, phone):
    volume = box.sim()["volume"]
    phone.volume_up()
    box.wait_sim(lambda s: s["volume"] > volume, "louder")
    phone.media("volume_down")
    box.wait_sim(lambda s: s["volume"] <= volume, "quieter")
    assert phone.set_landscape(True)["landscape"] is True
    assert phone.set_landscape(False)["landscape"] is False


def test_cli_options_after_the_command_and_exit_codes(box, capsys, monkeypatch):
    assert cli(["tap", "0.5", "0.5", "--url", box.url, "--token", box.token]) == 0
    monkeypatch.setenv("IHC_URL", box.url)
    monkeypatch.setenv("IHC_TOKEN", box.token)
    assert cli(["devices"]) == 0
    assert "iphone-sim" in capsys.readouterr().out
    assert cli(["screenshot", "/nonexistent-dir/shot.png"]) == 1
    assert "cannot write" in capsys.readouterr().err
    assert cli(["--url", "http://127.0.0.1:9", "devices"]) == 3
