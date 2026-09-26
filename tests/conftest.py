"""The SDK is tested against the box software itself: `ihcd serve --sim` (a simulated iPhone).

The binary comes from $IHCD, else it is built from box/ with Go."""

from __future__ import annotations

import os
import socket
import subprocess
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "test-token"


def _ihcd(tmp: Path) -> str:
    if os.environ.get("IHCD"):
        return os.environ["IHCD"]
    out = tmp / "ihcd"
    try:
        subprocess.run(["go", "build", "-o", str(out), "./cmd/ihcd"], cwd=ROOT / "box", check=True,
                       env={**os.environ, "GOTOOLCHAIN": "local"}, capture_output=True, text=True)
    except FileNotFoundError:
        pytest.fail("these tests need the box software: set $IHCD to an ihcd binary, or install Go to build it")
    except subprocess.CalledProcessError as e:
        pytest.fail(f"go build failed:\n{e.stderr}")
    return str(out)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Box:
    def __init__(self, url: str, token: str, binary: str):
        self.url, self.token, self.binary = url, token, binary

    def sim(self) -> dict:
        return httpx.get(f"{self.url}/api/devices/iphone-sim/sim", headers={"Authorization": f"Bearer {self.token}"}).json()

    def wait_sim(self, cond, what: str, timeout: float = 5.0) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            st = self.sim()
            if cond(st):
                return st
            if time.monotonic() > deadline:
                raise AssertionError(f"{what}: the phone shows {st}")
            time.sleep(0.02)


@pytest.fixture(scope="session")
def box(tmp_path_factory):
    binary = _ihcd(tmp_path_factory.mktemp("bin"))
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    proc = subprocess.Popen([binary, "serve", "--sim", "--addr", f"127.0.0.1:{port}", "--token", TOKEN, "--no-mdns"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    deadline = time.monotonic() + 15
    while True:
        try:
            if httpx.get(f"{url}/api/health", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        if proc.poll() is not None or time.monotonic() > deadline:
            proc.kill()
            raise AssertionError(f"ihcd did not start: {proc.stderr.read().decode()}")
        time.sleep(0.05)
    try:
        yield Box(url, TOKEN, binary)
    finally:
        proc.terminate()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()
