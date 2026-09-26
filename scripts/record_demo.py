"""Record the console demo videos in docs/media from a box with a simulated iPhone.

    pip install -e ".[demo]"          # playwright, websockets, imageio-ffmpeg
    python -m playwright install chromium
    python scripts/record_demo.py      # or: make demo

It builds `ihcd` from box/ (or takes --ihcd), runs `ihcd serve --sim` on a free port and drives the
console through every state it has: the token, touch and typing, the phone's buttons, a test script
running, the USB cable unplugged, the phone asleep, the HDMI signal lost, a second operator, too
many viewers, the box going away. The page gets a visible cursor and a caption for each step.
Frames come from Chromium's screencast and are encoded to H.264 MP4 with ffmpeg (imageio-ffmpeg's
binary, or ffmpeg on the PATH). A short GIF of the touch part is made for the README.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import websockets
from playwright.async_api import async_playwright

REPO = Path(__file__).resolve().parent.parent
TOKEN = "demo-token"
DEVICE = "iphone-b40d9e"

OVERLAY = r"""
(() => {
  const init = () => {
    const css = document.createElement('style');
    css.textContent = `
      #__cur,#__cap{inset:auto;border:0;overflow:visible}
      #__cur{position:fixed;left:-50px;top:-50px;width:22px;height:22px;margin:-11px 0 0 -11px;border-radius:50%;
        border:2px solid #fff !important;padding:0;background:rgba(255,255,255,.2);
        box-shadow:0 0 0 1px rgba(0,0,0,.55),0 2px 6px rgba(0,0,0,.4);
        pointer-events:none;transition:transform 70ms,background 70ms}
      #__cur.down{transform:scale(.75);background:rgba(255,196,0,.9);border-color:#ffc400 !important}
      #__cur.touch{opacity:0} #__cur.touch.down{opacity:1;width:38px;height:38px;margin:-19px 0 0 -19px}
      #__cap{position:fixed;left:16px;bottom:16px;margin:0;max-width:min(560px,calc(100vw - 32px));box-sizing:border-box;
        padding:10px 16px;border-radius:10px;background:rgba(10,12,14,.9);color:#fff;
        font:600 17px/1.35 "DejaVu Sans","Helvetica Neue",Arial,sans-serif;pointer-events:none;
        box-shadow:0 6px 24px rgba(0,0,0,.35);border-left:4px solid #ffc400 !important}
      #__cap small{display:block;font-weight:400;font-size:14px;opacity:.82;margin-top:4px}
      #__cap:empty{display:none}
      .__m body{padding-top:58px}
      .__m #__cap{font-size:14px;bottom:auto;top:8px;left:10px;right:10px;max-width:none;padding:7px 12px}
      .__m #__cap small{font-size:12px}`;
    document.head.appendChild(css);
    const cur = document.createElement('div'); cur.id = '__cur';
    const cap = document.createElement('div'); cap.id = '__cap';
    cur.popover = 'manual'; cap.popover = 'manual';
    document.body.append(cur, cap);
    // popovers sit in the top layer: shown again after a modal dialog opens, they stay above it
    const top = () => { for (const el of [cap, cur]) { try { el.hidePopover(); } catch (e) {} el.showPopover(); } };
    top();
    new MutationObserver(top).observe(document.body, { subtree: true, attributeFilter: ['open'] });
    const mv = (e) => { cur.style.left = e.clientX + 'px'; cur.style.top = e.clientY + 'px';
      cur.classList.toggle('touch', e.pointerType === 'touch'); };
    addEventListener('pointermove', mv, true);
    addEventListener('pointerdown', (e) => { mv(e); cur.classList.add('down'); }, true);
    addEventListener('pointerup', (e) => { mv(e); cur.classList.remove('down'); }, true);
    addEventListener('pointercancel', () => cur.classList.remove('down'), true);
    window.__cap = (t, sub) => {
      cap.textContent = t || '';
      if (t && sub) { const s = document.createElement('small'); s.textContent = sub; cap.appendChild(s); }
    };
    try { const c = sessionStorage.getItem('__cap'); if (c) window.__cap(...JSON.parse(c)); } catch (e) {}
    if (matchMedia('(pointer: coarse)').matches) document.documentElement.classList.add('__m');
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
"""


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def ffmpeg_path() -> str:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        path = shutil.which("ffmpeg")
        if not path:
            sys.exit("needs ffmpeg: pip install imageio-ffmpeg, or ffmpeg on the PATH")
        return path


class Box:
    """`ihcd serve --sim` on a port of its own, stopped and started again for the unreachable box."""

    def __init__(self, ihcd: str, port: int):
        self.ihcd, self.port, self.proc = ihcd, port, None
        self.base = f"http://127.0.0.1:{port}"
        self.dev = f"{self.base}/api/devices/{DEVICE}"
        self.ws = f"ws://127.0.0.1:{port}/api/devices/{DEVICE}"

    def start(self):
        self.proc = subprocess.Popen(
            [self.ihcd, "serve", "--sim", "--no-mdns", "--addr", f"127.0.0.1:{self.port}", "--id", DEVICE,
             "--token", TOKEN], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                if self.call("GET", "")["state"] == "ready":
                    return
            except (OSError, KeyError):
                pass
            time.sleep(0.1)
        sys.exit("the simulated box did not start")

    def stop(self):
        if self.proc:
            self.proc.terminate()
            self.proc.wait(10)
            self.proc = None

    def call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.dev + path, data=data, method=method,
                                     headers={"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            return json.load(e)

    async def api(self, method, path, body=None):
        return await asyncio.to_thread(self.call, method, path, body)


class Recorder:
    """Chromium's screencast frames, with their times, then an MP4."""

    def __init__(self, page, work: Path, name: str, size: tuple[int, int]):
        self.page, self.name, self.size = page, name, size
        self.dir = work / name
        self.dir.mkdir(parents=True)
        self.frames: list[tuple[float, Path]] = []
        self.chapters: list[tuple[float, str]] = []

    async def start(self):
        self.cdp = await self.page.context.new_cdp_session(self.page)
        self.cdp.on("Page.screencastFrame", self.frame)
        await self.cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 90, "maxWidth": self.size[0],
                                                     "maxHeight": self.size[1], "everyNthFrame": 1})

    def frame(self, p):
        path = self.dir / f"f{len(self.frames):06d}.jpg"
        path.write_bytes(base64.b64decode(p["data"]))
        self.frames.append((p["metadata"]["timestamp"], path))
        asyncio.ensure_future(self.cdp.send("Page.screencastFrameAck", {"sessionId": p["sessionId"]}))

    def chapter(self, title):
        self.chapters.append((time.time(), title))

    async def stop(self, ffmpeg: str, out: Path) -> float:
        await self.cdp.send("Page.stopScreencast")
        await asyncio.sleep(0.2)
        lst = self.dir / "list.txt"
        with lst.open("w") as f:
            f.write("ffconcat version 1.0\n")
            for i, (t, path) in enumerate(self.frames):
                d = (self.frames[i + 1][0] - t) if i + 1 < len(self.frames) else 1.5
                f.write(f"file '{path}'\nduration {max(d, 0.001):.4f}\n")
            f.write(f"file '{self.frames[-1][1]}'\n")
        subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                        "-vf", "fps=30,scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p",
                        "-c:v", "libx264", "-preset", "slow", "-crf", "22", "-movflags", "+faststart", str(out)],
                       check=True)
        return self.frames[0][0]


class Stage:
    """One page: a cursor that glides, captions, touches on the phone's screen."""

    def __init__(self, page, rec: Recorder):
        self.page, self.rec = page, rec
        self.x, self.y = 700, 450

    async def cap(self, title, sub=""):
        if title:
            self.rec.chapter(title)
        await self.page.evaluate("([t, s]) => { sessionStorage.setItem('__cap', JSON.stringify([t, s])); "
                                 "window.__cap && window.__cap(t, s); }", [title, sub])

    async def wait(self, ms):
        await self.page.wait_for_timeout(ms)

    async def glide(self, x, y, ms=450):
        n = max(1, ms // 16)
        x0, y0 = self.x, self.y
        for i in range(1, n + 1):
            t = i / n
            e = t * t * (3 - 2 * t)
            await self.page.mouse.move(x0 + (x - x0) * e, y0 + (y - y0) * e)
            await asyncio.sleep(0.016)
        self.x, self.y = x, y

    async def at(self, fx, fy):
        b = await self.page.locator("#screen").bounding_box()
        return b["x"] + fx * b["width"], b["y"] + fy * b["height"]

    async def tap(self, fx, fy, ms=450):
        x, y = await self.at(fx, fy)
        await self.glide(x, y, ms)
        await self.wait(150)
        await self.page.mouse.down()
        await self.wait(90)
        await self.page.mouse.up()

    async def drag(self, f1, f2, ms=300, hold=0):
        x1, y1 = await self.at(*f1)
        x2, y2 = await self.at(*f2)
        await self.glide(x1, y1, 400)
        await self.wait(150)
        await self.page.mouse.down()
        await self.wait(hold or 60)
        await self.glide(x2, y2, ms)
        await self.page.mouse.up()

    async def click(self, selector, ms=500):
        b = await self.page.locator(selector).first.bounding_box()
        await self.glide(b["x"] + b["width"] / 2, b["y"] + b["height"] / 2, ms)
        await self.wait(120)
        await self.page.mouse.down()
        await self.wait(80)
        await self.page.mouse.up()

    async def state(self, want, timeout=8000):
        await self.page.wait_for_function("(w) => document.getElementById('state').dataset.state === w", arg=want,
                                          timeout=timeout)


# Home screen icons, as fractions of the screen: Settings, Notes.
SETTINGS, NOTES = (0.148, 0.142), (0.374, 0.142)


async def desktop(pw, box: Box, work: Path, ffmpeg: str, out: Path) -> Recorder:
    browser = await pw.chromium.launch()
    ctx = await browser.new_context(viewport={"width": 1440, "height": 900})
    await ctx.add_init_script(OVERLAY)
    page = await ctx.new_page()
    await page.goto(box.base + "/")
    rec = Recorder(page, work, "desktop", (1440, 900))
    s = Stage(page, rec)
    await rec.start()

    await s.cap("First visit: the box token", "It is in /var/lib/ihc/token on the box")
    await s.wait(1800)
    await page.locator("#token-input").type("wrong-token", delay=70)
    await s.wait(300)
    await page.locator("#token-form button").click()
    await s.cap("A wrong token", "The box refuses it and the console asks again")
    await s.wait(2200)
    await page.locator("#token-input").type(TOKEN, delay=70)
    await s.wait(300)
    await page.locator("#token-form button").click()
    await s.cap("The right token: connected", "The browser keeps it")
    await s.state("ready")
    await s.wait(1800)

    await s.cap("The guide: this box now", "The wiring, with the state of each cable, and what to check")
    await s.click("#guide-open")
    await s.wait(6800)
    await s.cap("The guide: wiring with a USB-C hub", "Each cable in order; dots show the picture, touch and power")
    await s.click("[data-tab=hub]")
    await s.wait(7000)
    await s.cap("The guide: the purpose-built box", "One cable to the iPhone (in design)")
    await s.click("[data-tab=box]")
    await s.wait(4500)
    await s.cap("The guide: using the console", "")
    await s.click("[data-tab=use]")
    await s.wait(5000)
    await s.click("#guide-close")
    await s.wait(600)

    await s.cap("Hover moves the iPhone's pointer", "The AssistiveTouch pointer follows the mouse as it moves")
    for fx, fy in [(0.3, 0.4), (0.7, 0.45), (0.6, 0.25), (0.25, 0.3)]:
        x, y = await s.at(fx, fy)
        await s.glide(x, y, 600)
    await s.wait(400)
    await s.cap("Click to tap", "Opens Notes")
    await s.tap(*NOTES)
    await s.wait(1200)

    await s.cap("Type straight onto the iPhone", "After a click on the screen, keys go to the phone; the device is outlined")
    await page.keyboard.type("hello from the console", delay=85)
    await page.keyboard.press("Enter")
    await s.wait(1200)

    await s.cap("Type a whole text from the panel", "Then the send button; or paste with Cmd/Ctrl+V on the screen")
    b = await page.locator("#type-text").bounding_box()
    await s.glide(b["x"] + 60, b["y"] + b["height"] / 2, 600)
    await page.locator("#type-text").click()
    await page.locator("#type-text").type("Typed from the panel: 42", delay=45)
    await s.click("#type-form button")
    await s.wait(2600)

    await s.cap("Home", "The button under the phone (Cmd+H to the iPhone)")
    await s.click("[data-button=home]")
    await s.wait(1200)
    await s.cap("Drag to swipe: home screen pages", "The swipe happens while the mouse moves")
    await s.drag((0.85, 0.55), (0.15, 0.55), ms=260)
    await s.wait(1300)
    await s.drag((0.15, 0.55), (0.85, 0.55), ms=260)
    await s.wait(1200)

    await s.cap("Drag a list and let go", "It keeps its speed, as under a finger")
    await s.tap(*SETTINGS)
    await s.wait(900)
    await s.drag((0.5, 0.78), (0.5, 0.4), ms=220)
    await s.wait(1800)
    await s.drag((0.5, 0.35), (0.5, 0.6), ms=500, hold=100)
    await s.wait(1000)
    await s.cap("The wheel scrolls")
    x, y = await s.at(0.5, 0.5)
    await s.glide(x, y, 300)
    for _ in range(8):
        await page.mouse.wheel(0, 120)
        await s.wait(140)
    await s.wait(900)

    await s.cap("The volume buttons on the phone's edge", "They press the iPhone's own volume keys (USB media keys)")
    for _ in range(3):
        await s.click(".hw-vol-up", 350)
        await s.wait(250)
    await s.click(".hw-vol-down", 400)
    await s.wait(700)
    await s.cap("Mute, from the Media panel")
    await s.click(".panel [data-button=mute]")
    await s.wait(1600)

    await s.cap("App Switcher", "The pointer's middle button, mapped to App Switcher in AssistiveTouch")
    await s.click("[data-button=app_switcher]")
    await s.wait(1800)
    await s.cap("Search", "Cmd+Space, then type")
    await s.click("[data-button=spotlight]")
    await s.wait(500)
    await page.focus("#screen")
    await page.keyboard.type("maps", delay=110)
    await s.wait(1300)
    await s.click("[data-button=home]")
    await s.wait(900)

    await s.cap("A test script runs over the API", "The state reads Busy; the console shows the script typing")
    await box.api("POST", "/tap", {"x": NOTES[0], "y": NOTES[1]})
    typing = asyncio.ensure_future(box.api("POST", "/type", {"text": "\nThis line comes from a test script over the API."}))
    await s.state("busy")
    await typing
    await s.wait(1600)

    await s.cap("The USB cable is unplugged", "Not connected, with what to check")
    await box.api("POST", "/sim", {"usb": "unplugged"})
    await s.state("no_usb")
    await s.wait(1500)
    await s.cap("What to check", "The guide shows the cable at fault on the wiring")
    await s.click("#state-msg button[aria-label='Show what to check']")
    await s.wait(6500)
    await s.click("#guide-close")
    await s.wait(500)
    await s.cap("A button pressed while unplugged", "The console says why; nothing reaches the phone")
    await s.click("[data-button=home]")
    await s.wait(2500)
    await s.cap("Plugged in again", "Back to Ready by itself")
    await box.api("POST", "/sim", {"usb": "connected"})
    await s.state("ready")
    await s.wait(1500)

    await s.cap("The iPhone falls asleep", "Asleep, with a Wake button")
    await box.api("POST", "/sim", {"usb": "asleep"})
    await s.state("asleep")
    await s.wait(1800)
    await s.cap("Wake", "The box wakes the iPhone over USB (remote wakeup)")
    await s.click("#state-msg button[aria-label='Wake the phone']")
    await s.state("ready")
    await s.wait(1600)

    await s.cap("The HDMI signal is lost", "No picture: the last frame is greyed")
    await box.api("POST", "/sim", {"video": "no_signal"})
    await s.state("no_video")
    await s.wait(2600)
    await s.cap("The picture is back")
    await box.api("POST", "/sim", {"video": "ok"})
    await s.state("ready")
    await s.wait(1600)

    await s.cap("Another operator takes control", "One operator controls a phone at a time; the other is told")
    other = await websockets.connect(box.ws + f"/control?token={TOKEN}&takeover=true")
    await page.locator("#banner").wait_for(state="visible")
    await s.wait(2200)
    await s.cap("Take over to get it back")
    await s.click("#banner-action")
    await s.wait(1500)
    await other.close()
    await s.cap("Opening the console while someone else controls the phone")
    other = await websockets.connect(box.ws + f"/control?token={TOKEN}&takeover=true")
    await s.wait(600)
    await page.reload()
    await page.locator("#banner").wait_for(state="visible")
    await s.wait(2200)
    await s.click("#banner-action")
    await s.wait(1400)
    await other.close()

    await s.cap("Too many viewers", "A box streams to at most 4 viewers at once")
    viewers = [await websockets.connect(box.ws + f"/stream?token={TOKEN}") for _ in range(3)]

    async def grab():
        # the box accepts the socket, then closes it with 4429 when it is full: a status message means a place
        while True:
            try:
                v = await websockets.connect(box.ws + f"/stream?token={TOKEN}")
                await asyncio.wait_for(v.recv(), 1)
                viewers.append(v)
                return
            except Exception:
                await asyncio.sleep(0.02)

    grabber = asyncio.ensure_future(grab())
    await page.reload()
    await grabber
    await page.wait_for_function("document.getElementById('screen-note').textContent.includes('Too many')")
    await s.wait(2600)
    await s.cap("A viewer leaves: the picture comes back")
    for v in viewers:
        await v.close()
    await page.wait_for_function("document.getElementById('screen-note').hidden", timeout=10000)
    await s.wait(1500)

    await s.cap("The box loses power or network", "Box unreachable; the console keeps trying")
    box.stop()
    await s.state("offline")
    await s.wait(3000)
    await s.cap("The box is back", "The console reconnects without a reload")
    box.start()
    await s.state("ready", timeout=15000)
    await s.wait(2000)
    await s.cap("")
    await s.wait(400)

    start = await rec.stop(ffmpeg, out)
    rec.chapters = [(t - start, title) for t, title in rec.chapters]
    await browser.close()
    return rec


async def touch(cdp, kind, x=None, y=None):
    pts = [] if kind == "touchEnd" else [{"x": x, "y": y, "radiusX": 8, "radiusY": 8, "force": 1, "id": 1}]
    await cdp.send("Input.dispatchTouchEvent", {"type": kind, "touchPoints": pts})


async def mobile(pw, box: Box, work: Path, ffmpeg: str, out: Path):
    browser = await pw.chromium.launch()
    ctx = await browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True,
                                    has_touch=True)
    await ctx.add_init_script(OVERLAY)
    page = await ctx.new_page()
    await page.goto(box.base + "/")
    rec = Recorder(page, work, "mobile", (780, 1688))
    s = Stage(page, rec)
    await rec.start()
    cdp = rec.cdp

    async def tap(fx, fy):
        x, y = await s.at(fx, fy)
        await touch(cdp, "touchStart", x, y)
        await s.wait(90)
        await touch(cdp, "touchEnd")

    async def swipe(f1, f2, ms=260):
        x1, y1 = await s.at(*f1)
        x2, y2 = await s.at(*f2)
        await touch(cdp, "touchStart", x1, y1)
        n = max(2, ms // 16)
        for i in range(1, n + 1):
            t = i / n
            await touch(cdp, "touchMove", x1 + (x2 - x1) * t, y1 + (y2 - y1) * t)
            await asyncio.sleep(0.016)
        await touch(cdp, "touchEnd")

    await s.cap("On a phone", "Enter the token once")
    await s.wait(1200)
    await page.locator("#token-input").type(TOKEN, delay=80)
    await page.locator("#token-form button").tap()
    await s.state("ready")
    await s.wait(1500)
    await s.cap("Touch to tap", "Opens Settings")
    await tap(*SETTINGS)
    await s.wait(1000)
    await s.cap("Swipe to swipe", "A list keeps its speed")
    await swipe((0.5, 0.8), (0.5, 0.35), 200)
    await s.wait(1800)
    await s.cap("The phone's buttons", "Home")
    await page.locator("[data-button=home]").first.tap()
    await s.wait(1000)
    await swipe((0.85, 0.5), (0.15, 0.5), 240)
    await s.wait(1300)
    await swipe((0.15, 0.5), (0.85, 0.5), 240)
    await s.wait(1000)
    await s.cap("Type from the panel", "The panels sit under the phone")
    await tap(*NOTES)
    await s.wait(800)
    await page.evaluate("document.getElementById('type-text').scrollIntoView({behavior: 'smooth', block: 'center'})")
    await s.wait(1200)
    await page.locator("#type-text").tap()
    await page.locator("#type-text").type("typed on a phone", delay=60)
    await page.locator("#type-form button").tap()
    await s.wait(900)
    await page.evaluate("window.scrollTo({top: 0, behavior: 'smooth'})")
    await s.wait(2200)
    await s.cap("")
    await s.wait(300)
    await rec.stop(ffmpeg, out)
    await browser.close()


def gif(ffmpeg: str, video: Path, chapters, first: str, last: str, out: Path):
    """A GIF of the chapters from `first` up to the one after `last`, cropped to the phone's side."""
    starts = [t for t, title in chapters]
    names = [title for t, title in chapters]
    a = starts[names.index(first)]
    i = names.index(last) + 1
    b = starts[i] if i < len(starts) else a + 12
    graph = ("crop=900:900:0:0,fps=12,scale=600:-1:flags=lanczos,split[a][b];"
             "[a]palettegen=max_colors=128:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4")
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-ss", f"{a:.2f}", "-t", f"{b - a:.2f}", "-i", str(video),
                    "-filter_complex", graph, "-loop", "0", str(out)], check=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ihcd", help="the ihcd binary (default: build it from box/)")
    ap.add_argument("--out", default=str(REPO / "docs" / "media"), help="where the videos go")
    ap.add_argument("--only", choices=["desktop", "mobile"], help="record one of the two")
    args = ap.parse_args()
    ffmpeg = ffmpeg_path()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        ihcd = args.ihcd
        if not ihcd:
            ihcd = str(work / "ihcd")
            subprocess.run(["go", "build", "-o", ihcd, "./cmd/ihcd"], cwd=REPO / "box", check=True)
        box = Box(ihcd, free_port())

        async def run():
            async with async_playwright() as pw:
                if args.only in (None, "desktop"):
                    box.start()
                    rec = await desktop(pw, box, work, ffmpeg, out / "console-demo.mp4")
                    box.stop()
                    gif(ffmpeg, out / "console-demo.mp4", rec.chapters, "Drag to swipe: home screen pages",
                        "The wheel scrolls", out / "console-demo.gif")
                if args.only in (None, "mobile"):
                    box.start()
                    await mobile(pw, box, work, ffmpeg, out / "console-demo-mobile.mp4")
                    box.stop()

        try:
            asyncio.run(run())
        finally:
            box.stop()
    for f in sorted(out.glob("console-demo*")):
        print(f"{f.relative_to(REPO) if f.is_relative_to(REPO) else f}: {f.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
