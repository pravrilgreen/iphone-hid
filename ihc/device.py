"""IPhoneDevice: one phone = HID backend + video source + pointer model.

Public coordinates are normalized (0..1 across the phone screen) or points; "frame" coordinates (a
pixel of the captured video, e.g. where an operator clicked on the live view) are converted through
the screen rectangle, which is pure geometry (scale-to-fit, centred) unless the rig config overrides it.

Actions on one device run one at a time (a per-device lock), live remote-control input included;
status() never waits for them. A monitor thread keeps the health fresh and reopens the serial port
after an unplug.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from .calibration import ClickCollector, calibrate
from .hid import protocol as p
from .hid.base import HidError, HidPortError
from .hid.ch9329 import CH9329Backend
from .input.keyboard import Keyboard
from .input.pointer import PointerCalibration, PointerError, PointerModel
from .video.frame import Frame, FrameSource, encode_jpeg
from .video.geometry import ScreenRect, fit_screen_rect

STATES = ("ready", "busy", "hid_disconnected", "hid_offline", "no_signal")


@dataclass
class DeviceInfo:
    id: str
    model: str = ""
    kind: str = "hardware"  # or "sim"


class IPhoneDevice:
    def __init__(
        self,
        info: DeviceInfo,
        hid: CH9329Backend,
        source: FrameSource,
        *,
        calibration: PointerCalibration | None = None,
        calibration_path: str | Path | None = None,
        screen_rect: ScreenRect | None = None,
        reopen_hid: Callable[[], CH9329Backend] | None = None,
        log=None,
    ):
        self.info = info
        self.hid = hid
        self.source = source
        self.calibration_path = Path(calibration_path) if calibration_path else None
        if calibration is None and self.calibration_path and self.calibration_path.exists():
            calibration = PointerCalibration.load(self.calibration_path)
        self.pointer = PointerModel(hid, calibration)
        self.keyboard = Keyboard(hid)
        self.clicks = ClickCollector()
        self._rect_override = screen_rect
        self._reopen_hid = reopen_hid
        self._log = log or (lambda event, **fields: None)
        self._action = threading.RLock()
        self._busy_with: str | None = None
        self._health = {"hid": None, "usb_connected": None, "signal": None, "error": None}
        self._monitor: threading.Thread | None = None
        self._stop = threading.Event()
        self.counters = {"actions": 0, "errors": 0, "taps": 0, "live_reports": 0}
        self.last_result: dict | None = None

    @property
    def id(self) -> str:
        return self.info.id

    # -- health ---------------------------------------------------------------------------------

    def check(self) -> dict:
        """Refresh health: chip reachable and its USB side enumerated, video frames present and not
        black. Skipped (cached value returned) while an action runs."""
        if not self._action.acquire(blocking=False):
            return self._health
        try:
            h = {"hid": None, "usb_connected": None, "signal": None, "error": None}
            try:
                h["usb_connected"] = self.hid.info()["usb_connected"]
                h["hid"] = True
            except HidPortError as e:
                h["hid"], h["error"] = False, str(e)
                self._try_reopen()
            except HidError as e:
                h["hid"], h["error"] = False, str(e)
            try:
                h["signal"] = not _is_blank(self.source.latest(timeout=1.0), self.screen_rect())
            except (TimeoutError, OSError, ValueError) as e:
                h["signal"] = False
                h["error"] = h["error"] or f"video: {e}"
            self._health = h
            return h
        finally:
            self._action.release()

    @property
    def state(self) -> str:
        if self._busy_with:
            return "busy"
        h = self._health
        if h["hid"] is False:
            return "hid_offline"
        if h["usb_connected"] is False:
            return "hid_disconnected"  # phone locked, accessory prompt, cable
        if h["signal"] is False:
            return "no_signal"
        return "ready"

    def status(self) -> dict:
        cal = self.pointer.cal
        pos = self.pointer.position
        w, h = cal.screen_pt
        video = self.source.stats() if hasattr(self.source, "stats") else {"size": list(self.source.size)}
        return {
            "id": self.id,
            "model": self.info.model,
            "kind": self.info.kind,
            "state": self.state,
            "busy_with": self._busy_with,
            "health": dict(self._health),
            "screen": {"points": [w, h], "rect": self.screen_rect().to_dict()},
            "pointer": {"mode": cal.mode,
                        "pt": [round(v, 1) for v in pos] if pos else None,
                        "norm": [round(pos[0] / w, 4), round(pos[1] / h, 4)] if pos else None,
                        "reports": self.pointer.reports, "resends": self.pointer.resends + self.keyboard.resends},
            "calibration": {"calibrated": cal.calibrated, "method": cal.method, "measured_at": cal.measured_at,
                            "validation": cal.extra.get("validation_error_pt")},
            "hid": {"port": self.hid.port, "baud": self.hid.baud, "stats": dict(self.hid.stats)},
            "video": video,
            "counters": dict(self.counters),
            "last_result": self.last_result,
        }

    def start_monitor(self, interval: float = 2.0) -> None:
        if self._monitor is not None:
            return

        def run() -> None:
            while not self._stop.wait(interval):
                try:
                    self.check()
                except Exception as e:  # keep monitoring whatever happens
                    self._health = dict(self._health, error=f"monitor: {e}")

        self._monitor = threading.Thread(target=run, name=f"monitor-{self.id}", daemon=True)
        self._monitor.start()

    def _try_reopen(self) -> None:
        if self._reopen_hid is None:
            return
        try:
            new = self._reopen_hid()
        except HidError:
            return
        try:
            self.hid.close()
        except Exception:
            pass
        self.hid = self.pointer.hid = self.keyboard.hid = new
        self.pointer.position = None
        self._log("hid_reopened", device=self.id, port=new.port)

    def close(self) -> None:
        self._stop.set()
        if self._monitor is not None:
            self._monitor.join(timeout=3)
        with self._action:
            try:
                self.hid.release_all()
            except HidError:
                pass
            self.hid.close()
        self.source.close()

    # -- running actions ------------------------------------------------------------------------

    def _run(self, name: str, fn, **fields) -> dict:
        with self._action:
            self._busy_with = name
            t0 = time.monotonic()
            reports0 = self.pointer.reports
            try:
                result = fn() or {}
                self.counters["actions"] += 1
                self.last_result = {"action": name, **fields, "ok": True, **result,
                                    "reports": self.pointer.reports - reports0,
                                    "seconds": round(time.monotonic() - t0, 3)}
                self._log("action", device=self.id, **self.last_result)
                return self.last_result
            except (HidError, PointerError, ValueError, RuntimeError) as e:
                self.counters["errors"] += 1
                self.last_result = {"action": name, **fields, "ok": False, "error": str(e)}
                self._log("action", device=self.id, **self.last_result)
                raise
            finally:
                self._busy_with = None

    def _pt(self, x: float, y: float, space: str) -> tuple[float, float]:
        w, h = self.pointer.cal.screen_pt
        if space == "norm":
            return x * w, y * h
        if space == "pt":
            return x, y
        if space == "frame":
            nx, ny = self.screen_rect().frame_to_norm(x, y)
            return nx * w, ny * h
        raise ValueError(f"unknown coordinate space {space!r} (norm, pt or frame)")

    # -- screen ---------------------------------------------------------------------------------

    def screen_rect(self) -> ScreenRect:
        return self._rect_override or fit_screen_rect(self.source.size, self.pointer.cal.screen_pt)

    def frame(self, newer_than: int = -1, timeout: float = 1.0) -> Frame:
        return self.source.latest(newer_than=newer_than, timeout=timeout)

    def screenshot(self, fmt: str = "jpeg", crop: bool = True, quality: int = 85) -> bytes:
        """The current screen, cropped to the phone (default) or the whole capture frame."""
        f = self.frame()
        if not crop and fmt == "jpeg":
            return f.to_jpeg(quality)
        img = f.image
        if crop:
            c0, r0, c1, r1 = self.screen_rect().clipped_to(f.width, f.height).as_int_box()
            img = img[r0:r1, c0:c1]
        if fmt == "png":
            ok, buf = cv2.imencode(".png", img)
            if not ok:
                raise ValueError("PNG encoding failed")
            return buf.tobytes()
        return encode_jpeg(img, quality)

    # -- actions --------------------------------------------------------------------------------

    def tap(self, x: float, y: float, *, space: str = "norm", long: bool = False) -> dict:
        px, py = self._pt(x, y, space)

        def do():
            plan = self.pointer.move_to(px, py)
            self.pointer.click(p.MOUSE_LEFT, hold=0.8 if long else 0.06)
            self.counters["taps"] += 1
            return {"plan": plan.to_dict()}

        return self._run("long_press" if long else "tap", do, x=round(px, 1), y=round(py, 1))

    def move(self, x: float, y: float, *, space: str = "norm") -> dict:
        px, py = self._pt(x, y, space)
        return self._run("move", lambda: {"plan": self.pointer.move_to(px, py).to_dict()}, x=round(px, 1), y=round(py, 1))

    def swipe(self, x1: float, y1: float, x2: float, y2: float, *, space: str = "norm", duration: float = 0.3,
              hold_end: float = 0.0) -> dict:
        """Press at the start point, drag to the end point, release. `hold_end` > 0 pauses before
        lifting, which turns a fling into a precise drag. `duration` applies in absolute mode;
        relative drags take the time their calibrated runs need."""
        a, b = self._pt(x1, y1, space), self._pt(x2, y2, space)

        def do():
            plan = self.pointer.move_to(*a)
            self.pointer.press(p.MOUSE_LEFT)
            try:
                time.sleep(0.05)
                self.pointer.drag_by(b[0] - a[0], b[1] - a[1], duration)
                if hold_end:
                    time.sleep(hold_end)
            finally:
                self.pointer.release(p.MOUSE_LEFT)
            return {"plan": plan.to_dict()}

        return self._run("swipe", do, x1=round(a[0], 1), y1=round(a[1], 1), x2=round(b[0], 1), y2=round(b[1], 1))

    def scroll(self, x: float, y: float, amount: int, *, space: str = "norm") -> dict:
        px, py = self._pt(x, y, space)

        def do():
            plan = self.pointer.move_to(px, py)
            self.pointer.scroll(amount)
            return {"plan": plan.to_dict()}

        return self._run("scroll", do, x=round(px, 1), y=round(py, 1), amount=amount)

    def type(self, text: str) -> dict:
        return self._run("type", lambda: self.keyboard.type(text), chars=len(text))

    def key(self, combo: str) -> dict:
        return self._run("key", lambda: self.keyboard.key(combo), combo=combo)

    def home(self) -> dict:
        """Secondary button, mapped to Home in AssistiveTouch."""
        return self._run("home", lambda: self.pointer.click(p.MOUSE_RIGHT))

    def app_switcher(self) -> dict:
        """Middle button, mapped to App Switcher in AssistiveTouch."""
        return self._run("app_switcher", lambda: self.pointer.click(p.MOUSE_MIDDLE))

    def media(self, name: str) -> dict:
        if name not in p.MEDIA_KEYS:
            raise ValueError(f"unknown media key {name!r}; one of {', '.join(p.MEDIA_KEYS)}")

        def do():
            self.hid.media(p.MEDIA_KEYS[name])
            try:
                time.sleep(0.08)
            finally:
                self.hid.media(0)

        return self._run("media", do, key=name)

    def open_url(self, url: str) -> dict:
        """Spotlight, type the URL, Return: opens Safari on it."""

        def do():
            self.keyboard.key("cmd+space")
            time.sleep(0.4)
            self.keyboard.type(url + "\n")

        return self._run("open_url", do, url=url)

    def calibrate(self, page_url: str | None = None, **options) -> dict:
        """Measure the pointer with the calibration page (see ihc.calibration). With `page_url`
        the page is opened through Spotlight first; otherwise it must already be open."""
        self.clicks = ClickCollector()
        if page_url:
            self.open_url(page_url)

        def do():
            cal = calibrate(self.pointer, self.clicks, log=self._log, **options)
            if self.calibration_path:
                cal.save(self.calibration_path)
            return {"calibration": {"method": cal.method, "reset_reports": cal.reset_reports,
                                    "validation": cal.extra.get("validation_error_pt")}}

        return self._run("calibrate", do)

    # -- live remote control ---------------------------------------------------------------------

    def live_mouse(self, dx: int, dy: int, buttons: int, wheel: int = 0) -> None:
        """One relative report straight from an operator's mouse (no planning, no pacing)."""
        with self._action:
            self.pointer.buttons = buttons & 0x07
            self.pointer.raw(max(-127, min(127, dx)), max(-127, min(127, dy)), max(-127, min(127, wheel)))
            self.counters["live_reports"] += 1

    def live_abs(self, nx: float, ny: float, buttons: int, wheel: int = 0) -> None:
        """One absolute report from an operator's mouse position over the live view (normalized
        coordinates). Only meaningful when the phone follows absolute reports (pointer mode)."""
        w, h = self.pointer.cal.screen_pt
        x, y = min(max(nx, 0.0), 1.0) * w, min(max(ny, 0.0), 1.0) * h
        with self._action:
            self.pointer.buttons = buttons & 0x07
            self.pointer.send_abs(*self.pointer.abs_grid(x, y), wheel=max(-127, min(127, wheel)))
            self.pointer.position = (x, y)
            self.counters["live_reports"] += 1

    def live_keys(self, mods: int, keys: list[int]) -> None:
        """One keyboard state report straight from an operator's keyboard."""
        with self._action:
            self.keyboard.report(mods & 0xFF, keys[:6])
            self.counters["live_reports"] += 1

    def release_all(self) -> None:
        with self._action:
            self.pointer.buttons = 0
            self.hid.release_all()


def _is_blank(frame: Frame, rect: ScreenRect) -> bool:
    """True when the phone area is (near) black: HDMI up but no picture. Decodes at 1/8 scale."""
    if frame.jpeg is not None:
        img = cv2.imdecode(np.frombuffer(frame.jpeg, np.uint8), cv2.IMREAD_REDUCED_GRAYSCALE_8)
        scale = 8.0
    else:
        img = cv2.cvtColor(frame.image, cv2.COLOR_BGR2GRAY)
        scale = 1.0
    if img is None:
        raise ValueError("undecodable frame")
    c0, r0, c1, r1 = rect.clipped_to(frame.width, frame.height).as_int_box()
    roi = img[int(r0 / scale):max(int(r0 / scale) + 1, int(r1 / scale)), int(c0 / scale):max(int(c0 / scale) + 1, int(c1 / scale))]
    return roi.size == 0 or int(roi.max()) < 32
