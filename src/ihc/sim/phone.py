"""A simulated iPhone: a small UI state machine driven by the HID events of a FakeChip.

It stands in for the phone during development without hardware. What it must get right is what the
host software depends on:
- input arrives only as relative pointer moves, clicks, drags, wheel and keyboard reports (through
  the simulated CH9329 on a pty, so the real driver code runs);
- the right and middle buttons act as Home and App Switcher (AssistiveTouch custom actions);
- Cmd+Space opens Spotlight, typing goes to the focused text field;
- UI changes are visible only after an animation delay;
- locking the phone or showing the wired-accessory prompt disconnects the HID side.
Everything else (look of the apps) is deliberately simple. Layout is in iOS points and is the single
source for hit testing and for the renderer.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from ..hid.fake import FakeChip
from ..models import MODELS, PhoneModel

GRID_APPS = ["Calendar", "Photos", "Camera", "Mail", "Clock", "Maps", "Weather", "Notes",
             "Reminders", "Settings", "App Store", "Targets"]
DOCK_APPS = ["Phone", "Safari", "Messages", "Music"]
ALL_APPS = GRID_APPS + DOCK_APPS
APP_COLORS = {
    "Calendar": (255, 255, 255), "Photos": (255, 149, 0), "Camera": (142, 142, 147), "Mail": (0, 122, 255),
    "Clock": (28, 28, 30), "Maps": (52, 199, 89), "Weather": (90, 200, 250), "Notes": (255, 204, 0),
    "Reminders": (255, 59, 48), "Settings": (142, 142, 147), "App Store": (10, 132, 255), "Targets": (255, 45, 85),
    "Phone": (52, 199, 89), "Safari": (0, 122, 255), "Messages": (48, 209, 88), "Music": (255, 45, 85),
}
SETTINGS_ROWS = ["Airplane Mode", "Wi-Fi", "Bluetooth", "Cellular", "Personal Hotspot", "Notifications",
                 "Sounds & Haptics", "Focus", "Screen Time", "General", "Control Center", "Display & Brightness",
                 "Home Screen & App Library", "Accessibility", "Wallpaper", "StandBy", "Siri", "Camera",
                 "Privacy & Security", "Battery", "App Store", "Wallet & Apple Pay", "Passwords", "Mail"]
SETTINGS_DETAIL = {
    "Accessibility": ["Touch", "AssistiveTouch: On", "Pointer Control", "Keyboards", "Display & Text Size"],
    "General": ["About", "Software Update", "Keyboard", "Language & Region", "Transfer or Reset iPhone"],
    "Display & Brightness": ["Appearance: Light", "Auto-Lock: Never", "Display Zoom: Default"],
    "Privacy & Security": ["Location Services", "Wired Accessories: Ask for New", "Lockdown Mode"],
}
LONG_PRESS_S = 0.5
ROW_H = 44.0
SAFARI_BAR_H = 50.0  # Safari's bottom address bar (iOS 15+ compact layout)
CALIBRATION_PATH = "/calibrate/"


@dataclass
class Element:
    """A rectangle of the current screen in points. `action` makes it tappable."""

    kind: str
    x: float
    y: float
    w: float
    h: float
    label: str = ""
    action: str = ""
    color: tuple[int, int, int] = (0, 122, 255)
    data: dict = field(default_factory=dict)

    def contains(self, px: float, py: float) -> bool:
        return self.x <= px <= self.x + self.w and self.y <= py <= self.y + self.h

    @property
    def center(self) -> tuple[float, float]:
        return self.x + self.w / 2, self.y + self.h / 2


@dataclass
class Layout:
    background: str  # "wallpaper", "light", "dark", "lock"
    elements: list[Element]
    overlay: list[Element] = field(default_factory=list)  # drawn on top; takes all taps when present
    dim: float = 0.0  # darkening of `elements` under the overlay
    hud: list[Element] = field(default_factory=list)  # drawn on top, never tappable


@dataclass
class Target:
    x: float
    y: float
    r: float
    hits: int = 0
    long_hits: int = 0


class SimPhone:
    def __init__(
        self,
        model: PhoneModel = MODELS["iphone-15"],
        *,
        dark: bool = False,
        right_button: str = "home",
        middle_button: str = "app_switcher",
        open_delay: float = 0.35,
        web_hover: bool = False,
        web_heartbeat: float | None = 0.25,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.model = model
        self.dark = dark
        self.right_button = right_button
        self.middle_button = middle_button
        self.open_delay = open_delay
        self._clock = clock
        self._lock = threading.RLock()
        self.chip: FakeChip | None = None
        self.screen = "home"
        self.detail: str | None = None
        self.overlay: str | None = None  # "spotlight" or "switcher"
        self.query = ""
        self.recents: list[str] = []
        self.locked = False
        self.accessory_prompt = False
        self.notes = ["Shopping list\nmilk, eggs, coffee", "Meeting notes"]
        self.editing: int | None = None  # index into notes, -1 for a new note
        self.editor_text = ""
        self.scroll = {"Settings": 0.0, "Notes": 0.0}
        self.volume = 0.5
        self.hud_until = 0.0
        self.targets = self._make_targets()
        self.tap_log: list[dict] = []
        self.url = "example.com"
        self.web_listeners: list[Callable[[str, dict], None]] = []  # (url, event) from web pages
        self.web_clicks: list[tuple[float, float]] = []
        # The calibration page (web/calibrate.html): it numbers and times its events, sends a
        # heartbeat every `web_heartbeat` s while shown and, if Safari reports hover (unverified on
        # iPhones, hence `web_hover`), hover positions at most every 100 ms. Time passes for it
        # through tick().
        self.web_hover = web_hover
        self.web_heartbeat = web_heartbeat
        self._web_seq = 0
        self._web_loaded = 0.0
        self._web_loads = 0
        self._web_pid = ""
        self._web_beat_at = math.inf
        self._web_seen = (math.nan, math.nan)  # pointer position at the previous tick
        self._web_moved_at = -math.inf  # latest hover report
        self.actions: list[str] = []
        self._last_click = (0.0, 0.0)
        self.version = 0
        self.changed_at = clock()

    # -- wiring --------------------------------------------------------------------------------

    def attach(self, chip: FakeChip) -> None:
        """Receive the chip's HID events; the chip's pointer is sized to this phone."""
        self.chip = chip
        chip.pointer.width, chip.pointer.height = float(self.model.width_pt), float(self.model.height_pt)
        chip.pointer.x, chip.pointer.y = self.model.width_pt / 2, self.model.height_pt / 2
        chip.listeners.append(self.handle)

    @property
    def top(self) -> float:
        return 54.0  # status bar

    @property
    def bottom_inset(self) -> float:
        return 34.0  # home indicator

    def _changed(self, delay: float = 0.0) -> None:
        self.version += 1
        self.changed_at = self._clock() + delay

    def _act(self, what: str) -> None:
        self.actions.append(what)
        if len(self.actions) > 1000:
            del self.actions[:500]

    # -- operator controls (things a person does to the phone) ---------------------------------

    def lock(self) -> None:
        with self._lock:
            self.locked = True
            self.overlay = None
            self._set_hid(False)
            self._changed()

    def unlock(self) -> None:
        with self._lock:
            self.locked = False
            self._set_hid(not self.accessory_prompt)
            self._changed()

    def prompt_accessory(self) -> None:
        """iOS asks whether to allow the wired accessory; HID is dead until someone answers."""
        with self._lock:
            self.accessory_prompt = True
            self._set_hid(False)
            self._changed()

    def allow_accessory(self) -> None:
        with self._lock:
            self.accessory_prompt = False
            self._set_hid(not self.locked)
            self._changed()

    def _set_hid(self, connected: bool) -> None:
        if self.chip is not None:
            self.chip.usb_connected = connected

    def open_app(self, name: str) -> None:
        with self._lock:
            self._open(name)

    def go_home(self) -> None:
        with self._lock:
            self._home()

    # -- event handling ------------------------------------------------------------------------

    def handle(self, e: dict) -> None:
        with self._lock:
            if self.locked or self.accessory_prompt:
                return
            kind = e["event"]
            if kind == "button_down" and e.get("button") == "left" and self._web_shown():
                # web/calibrate.html reports `pointerdown`: where the button went down, even when
                # the pointer then moves (a press during an absolute glide lands short, as on iOS)
                left, top, right, bottom = self.calibration_page_rect()
                if left <= e["x"] <= right and top <= e["y"] <= bottom:
                    self._last_click = (e["x"], e["y"])
                    self._web_event({"type": "click", "x": round(e["x"], 2), "y": round(e["y"] - top, 2), "button": 0})
            elif kind == "click":
                self._click(e)
            elif kind == "drag":
                self._drag(e)
            elif kind == "scroll":
                self._wheel(e["amount"])
            elif kind == "char":
                self._char(e["ch"])
            elif kind == "key":
                self._key(e["name"])
            elif kind == "shortcut":
                self._shortcut(e["combo"])
            elif kind == "media":
                self._media(e["keys"])

    def _click(self, e: dict) -> None:
        button, x, y = e["button"], e["x"], e["y"]
        if button != "left":
            self._button_action(self.right_button if button == "right" else self.middle_button)
            return
        long = e.get("held", 0.0) >= LONG_PRESS_S
        self._last_click = (x, y)
        el = self.element_at(x, y)
        if self.screen == "Targets" and self.overlay is None and (el is None or el.kind == "target"):
            self._target_tap(x, y, long)
            return
        if el is None:
            if self.overlay:
                self.overlay = None
                self._act("close_overlay")
                self._changed()
            return
        self._do(el.action, long=long)

    def _button_action(self, action: str) -> None:
        if action == "home":
            self._home()
        elif action == "app_switcher":
            self.overlay = "switcher" if self.overlay != "switcher" else None
            self._act("app_switcher")
            self._changed(0.25)

    def _do(self, action: str, long: bool = False) -> None:
        verb, _, arg = action.partition(":")
        self._act(action + (" (long)" if long else ""))
        if verb == "open":
            self._open(arg)
        elif verb == "home":
            self._home()
        elif verb == "back":
            if self.editing is not None:
                self._save_note()
            else:
                self.detail = None
            self._changed(0.25)
        elif verb == "row":
            if arg in SETTINGS_DETAIL:
                self.detail = arg
                self._changed(0.25)
        elif verb == "compose":
            self.editing, self.editor_text = -1, ""
            self._changed(0.25)
        elif verb == "note":
            self.editing = int(arg)
            self.editor_text = self.notes[self.editing]
            self._changed(0.25)
        elif verb == "done":
            self._save_note()
            self._changed(0.25)
        elif verb == "card":
            self.overlay = None
            self._open(arg)
        elif verb == "reset_targets":
            self.targets = self._make_targets()
            self.tap_log.clear()
            self._changed()
        elif verb == "field":
            pass
        elif verb == "web":
            pass  # reported when the button went down (see handle)

    def open_url(self, url: str) -> None:
        """Safari on `url` (what Spotlight does with a typed URL and Return)."""
        with self._lock:
            self._open("Safari")
            self.url = url
            self.web_clicks.clear()
            if CALIBRATION_PATH in url:
                self._web_loads += 1
                self._web_pid = f"load{self._web_loads}"  # the real page uses a random id per load
                self._web_seq, self._web_loaded = 0, self._clock()
                self._web_beat_at = self._web_loaded + (self.web_heartbeat or math.inf)
                self._web_event(self._web_hello())

    def calibration_page_rect(self) -> tuple[float, float, float, float]:
        """(left, top, right, bottom) of the calibration page on the screen, in points."""
        return 0.0, self.top, float(self.model.width_pt), self.top + self._web_page_h()

    def _web_page_h(self) -> float:
        return self.model.height_pt - self.top - self.bottom_inset - SAFARI_BAR_H

    def _web_hello(self) -> dict:
        W, H = self.model.width_pt, self.model.height_pt
        ev = {"type": "hello", "screen_w": W, "screen_h": H, "inner_w": W, "inner_h": self._web_page_h(),
              "ua": "SimPhone Safari"}
        if self.web_heartbeat:
            ev["heartbeat_ms"] = round(self.web_heartbeat * 1000)
        return ev

    def _web_shown(self) -> bool:
        return (self.screen == "Safari" and CALIBRATION_PATH in self.url and self.overlay is None
                and not self.locked and not self.accessory_prompt)

    def tick(self) -> None:
        """Time passing for the calibration page while Safari shows it: heartbeats and hover reports."""
        with self._lock:
            if not self._web_shown():
                return
            now = self._clock()
            if now >= self._web_beat_at:
                self._web_beat_at = now + self.web_heartbeat
                self._web_event(self._web_hello())
            if self.web_hover and self.chip is not None:
                x, y = self.chip.pointer.x, self.chip.pointer.y
                moved, self._web_seen = (x, y) != self._web_seen, (x, y)
                left, top, right, bottom = self.calibration_page_rect()
                if moved and now - self._web_moved_at >= 0.1 and top <= y <= bottom:
                    self._web_moved_at = now
                    self._web_event({"type": "move", "x": round(x, 2), "y": round(y - top, 2)})

    def _web_event(self, event: dict) -> None:
        """An event of the page Safari shows, numbered and timed as web/calibrate.html does."""
        self._web_seq += 1
        event = {**event, "pid": self._web_pid, "seq": self._web_seq,
                 "t": round((self._clock() - self._web_loaded) * 1000, 1)}
        if event["type"] == "click":
            self.web_clicks.append((event["x"], event["y"]))
            self._changed()
        for listener in list(self.web_listeners):
            listener(self.url, event)

    def _open(self, name: str) -> None:
        if name not in ALL_APPS:
            raise ValueError(f"no app {name!r}")
        self.overlay = None
        self.query = ""
        self.screen = name
        self.detail = None
        self.editing = None
        if name in self.recents:
            self.recents.remove(name)
        self.recents.insert(0, name)
        self._act(f"open:{name}")
        self._changed(self.open_delay)

    def _home(self) -> None:
        if self.editing is not None:
            self._save_note()
        self.screen, self.detail, self.overlay, self.query = "home", None, None, ""
        self._act("home")
        self._changed(self.open_delay)

    def _save_note(self) -> None:
        text = self.editor_text.strip("\n")
        if self.editing == -1 and text:
            self.notes.insert(0, text)
        elif self.editing is not None and self.editing >= 0:
            self.notes[self.editing] = text
        self.editing = None

    def _drag(self, e: dict) -> None:
        dy = e["y2"] - e["y1"]
        if e["button"] != "left":
            return
        if self.screen == "Targets" and self.overlay is None:
            self.tap_log.append({"kind": "swipe", "x1": e["x1"], "y1": e["y1"], "x2": e["x2"], "y2": e["y2"]})
        if e["y1"] > self.model.height_pt - 40 and dy < -60:
            self._home()  # swipe up from the home indicator
            return
        if self.overlay == "switcher":
            el = self.element_at(e["x1"], e["y1"])
            if el is not None and el.kind == "card" and dy < -80:
                self.recents.remove(el.label)
                self._act(f"close_app:{el.label}")
                self._changed(0.2)
            return
        if self.overlay is None and self.screen in self.scroll and self.detail is None and self.editing is None:
            self._scroll_by(-dy)

    def _wheel(self, amount: int) -> None:
        if self.overlay is None and self.screen in self.scroll and self.detail is None and self.editing is None:
            self._scroll_by(-amount * 30.0)  # positive = scroll up = content moves down

    def _scroll_by(self, d: float) -> None:
        rows = SETTINGS_ROWS if self.screen == "Settings" else self.notes
        content = len(rows) * ROW_H
        visible = self.model.height_pt - self.top - 110 - self.bottom_inset
        new = min(max(self.scroll[self.screen] + d, 0.0), max(0.0, content - visible))
        if new != self.scroll[self.screen]:
            self.scroll[self.screen] = new
            self._changed()

    def _char(self, ch: str) -> None:
        if self.overlay == "spotlight":
            if ch == "\n":
                if "://" in self.query:
                    self.open_url(self.query.strip())
                    return
                results = self.spotlight_results()
                if results:
                    self._open(results[0])
                return
            self.query += ch
            self._changed()
        elif self.editing is not None:
            self.editor_text += ch
            self._changed()

    def _key(self, name: str) -> None:
        if name == "backspace":
            if self.overlay == "spotlight" and self.query:
                self.query = self.query[:-1]
                self._changed()
            elif self.editing is not None and self.editor_text:
                self.editor_text = self.editor_text[:-1]
                self._changed()
        elif name == "esc" and self.overlay:
            self.overlay, self.query = None, ""
            self._act("close_overlay")
            self._changed(0.2)

    def _shortcut(self, combo: str) -> None:
        if combo == "cmd+space":
            self.overlay = None if self.overlay == "spotlight" else "spotlight"
            self.query = ""
            self._act("spotlight")
            self._changed(0.2)
        elif combo == "cmd+h":
            self._home()

    def _media(self, keys: list[str]) -> None:
        step = {"volume_up": 0.0625, "volume_down": -0.0625}
        for k in keys:
            if k in step:
                self.volume = min(max(self.volume + step[k], 0.0), 1.0)
                self.hud_until = self._clock() + 1.5
                self._changed()

    # -- Targets app (tap accuracy measurements) ----------------------------------------------

    def _make_targets(self) -> list[Target]:
        w, h = self.model.width_pt, self.model.height_pt
        top, bottom = self.top + 70, h - self.bottom_inset - 60
        cols, rows = 4, 7
        out = []
        for r in range(rows):
            for c in range(cols):
                x = w * (c + 0.5) / cols
                y = top + (bottom - top) * (r + 0.5) / rows
                out.append(Target(x, y, 14.0 if r % 2 == 0 else 8.0))
        return out

    def _target_tap(self, x: float, y: float, long: bool) -> None:
        best = min(range(len(self.targets)), key=lambda i: math.dist((x, y), (self.targets[i].x, self.targets[i].y)))
        t = self.targets[best]
        err = math.dist((x, y), (t.x, t.y))
        hit = err <= t.r
        if hit:
            t.hits += 1
            t.long_hits += int(long)
        self.tap_log.append({"kind": "tap", "target": best, "hit": hit, "error_pt": round(err, 2), "long": long, "x": x, "y": y})
        self._act(f"target:{best}:{'hit' if hit else 'miss'}")
        self._changed()

    def target_center_norm(self, index: int) -> tuple[float, float]:
        t = self.targets[index]
        return t.x / self.model.width_pt, t.y / self.model.height_pt

    # -- layout --------------------------------------------------------------------------------

    def element_at(self, x: float, y: float) -> Element | None:
        lay = self.layout()
        for el in reversed(lay.overlay or lay.elements):
            if el.action and el.contains(x, y):
                return el
        return None

    def find(self, label: str) -> Element | None:
        """Tappable element with this label on the topmost layer (for tests and demos)."""
        lay = self.layout()
        for el in lay.overlay or lay.elements:
            if el.action and el.label == label:
                return el
        return None

    def spotlight_results(self) -> list[str]:
        q = self.query.strip().lower()
        if not q:
            return (self.recents + [a for a in ALL_APPS if a not in self.recents])[:4]
        return [a for a in ALL_APPS if q in a.lower()][:6]

    def layout(self) -> Layout:
        with self._lock:
            if self.locked:
                return self._lock_layout()
            base = self._screen_layout()
            if self.overlay == "spotlight":
                base.overlay = self._spotlight_elements()
                base.dim = 0.82  # Spotlight blurs and darkens what is behind it
            elif self.overlay == "switcher":
                base = Layout("dark", self._switcher_elements())
            if self.accessory_prompt:
                base.overlay = self._prompt_elements()
                base.dim = max(base.dim, 0.4)
            if self._clock() < self.hud_until:
                base.hud.append(Element("hud", 12, self.top + 120, 10, 150, data={"level": self.volume}))
            return base

    def _screen_layout(self) -> Layout:
        W, H = self.model.width_pt, self.model.height_pt
        els: list[Element] = []
        bg = "dark" if self.dark else "light"
        if self.screen == "home":
            bg = "wallpaper"
            icon, cols = 62.0, 4
            gap = (W - cols * icon) / (cols + 1)
            for i, name in enumerate(GRID_APPS):
                r, c = divmod(i, cols)
                x, y = gap + c * (icon + gap), self.top + 18 + r * (icon + 34)
                els.append(Element("icon", x, y, icon, icon, name, f"open:{name}", APP_COLORS[name]))
            dock_h = 92.0
            dock_y = H - self.bottom_inset - dock_h - 6
            els.append(Element("dock", 10, dock_y, W - 20, dock_h))
            for i, name in enumerate(DOCK_APPS):
                x = gap + i * (icon + gap)
                els.append(Element("icon", x, dock_y + (dock_h - icon) / 2, icon, icon, name, f"open:{name}",
                                   APP_COLORS[name], {"dock": True}))
        elif self.screen == "Settings":
            if self.detail:
                els += self._nav("Settings", self.detail)
                for i, row in enumerate(SETTINGS_DETAIL[self.detail]):
                    els.append(Element("row", 16, self.top + 110 + i * ROW_H, W - 32, ROW_H, row, "noop"))
            else:
                els.append(Element("title", 16, self.top + 6, W - 32, 44, "Settings"))
                els += self._rows(SETTINGS_ROWS, "row")
        elif self.screen == "Notes":
            if self.editing is not None:
                els += self._nav("Notes", "")
                els.append(Element("button", W - 76, self.top + 4, 64, 36, "Done", "done"))
                els.append(Element("editor", 16, self.top + 56, W - 32, H - self.top - 56 - self.bottom_inset - 10,
                                   self.editor_text, "field"))
            else:
                els.append(Element("title", 16, self.top + 6, W - 32, 44, "Notes"))
                els += self._rows([n.split("\n")[0] or "New Note" for n in self.notes], "note", indexed=True)
                els.append(Element("button", W - 64, H - self.bottom_inset - 60, 48, 48, "Compose", "compose",
                                   APP_COLORS["Notes"]))
        elif self.screen == "Targets":
            els.append(Element("title", 16, self.top + 6, W - 150, 44, "Targets"))
            els.append(Element("button", W - 84, self.top + 10, 72, 34, "Reset", "reset_targets"))
            for i, t in enumerate(self.targets):
                els.append(Element("target", t.x - t.r, t.y - t.r, 2 * t.r, 2 * t.r, str(i), f"target:{i}",
                                   data={"hits": t.hits, "r": t.r}))
            for tap in self.tap_log[-40:]:
                if tap["kind"] == "tap" and not tap["hit"]:
                    els.append(Element("miss", tap["x"] - 3, tap["y"] - 3, 6, 6))
            taps = [t for t in self.tap_log if t["kind"] == "tap"]
            hits = sum(t["hit"] for t in taps)
            mean = sum(t["error_pt"] for t in taps) / len(taps) if taps else 0.0
            els.append(Element("caption", 16, H - self.bottom_inset - 40, W - 32, 24,
                               f"taps {len(taps)} · hits {hits} · mean error {mean:.1f} pt"))
        elif self.screen == "Safari" and CALIBRATION_PATH in self.url:
            page_h = self._web_page_h()
            els.append(Element("page", 0, self.top, W, page_h, "iphone-hid calibration", "web",
                               data={"clicks": self.web_clicks[-6:], "top": self.top}))
            els.append(Element("field", 16, H - self.bottom_inset - SAFARI_BAR_H + 6, W - 32, 38, self.url, "noop"))
        elif self.screen == "Safari":
            els.append(Element("field", 16, self.top + 8, W - 32, 40, self.url, "noop"))
            els.append(Element("heading", 20, self.top + 90, W - 40, 40, "Example Domain"))
            els.append(Element("paragraph", 20, self.top + 140, W - 40, 120,
                               "This domain is for use in illustrative examples in documents."))
            els.append(Element("link", 20, self.top + 270, 180, 28, "More information...", "noop"))
        else:
            els.append(Element("title", 16, self.top + 6, W - 32, 44, self.screen))
            els.append(Element("paragraph", 20, H / 2 - 30, W - 40, 60, f"{self.screen} (simulated)"))
        els.append(Element("indicator", W / 2 - 67, H - 13, 134, 5))
        return Layout(bg, els)

    def _nav(self, back: str, title: str) -> list[Element]:
        W = self.model.width_pt
        out = [Element("back", 8, self.top + 4, 110, 36, f"< {back}", "back")]
        if title:
            out.append(Element("title", 16, self.top + 48, W - 32, 44, title))
        return out

    def _rows(self, labels: list[str], verb: str, indexed: bool = False) -> list[Element]:
        W, H = self.model.width_pt, self.model.height_pt
        y0 = self.top + 60 - self.scroll.get(self.screen, 0.0)
        out = []
        for i, label in enumerate(labels):
            y = y0 + i * ROW_H
            if y + ROW_H < self.top + 56 or y > H - self.bottom_inset:
                continue  # scrolled out of view
            action = f"{verb}:{i if indexed else label}"
            out.append(Element("row", 16, y, W - 32, ROW_H, label, action))
        return out

    def _spotlight_elements(self) -> list[Element]:
        W = self.model.width_pt
        out = [Element("search", 12, self.top + 8, W - 24, 40, self.query, "field")]
        for i, name in enumerate(self.spotlight_results()):
            out.append(Element("result", 12, self.top + 60 + i * 56, W - 24, 52, name, f"open:{name}", APP_COLORS[name]))
        return out

    def _switcher_elements(self) -> list[Element]:
        W, H = self.model.width_pt, self.model.height_pt
        out = []
        cw, ch = W * 0.62, H * 0.62
        for i, name in enumerate(self.recents[:3]):
            x = W / 2 - cw / 2 + i * cw * 0.55  # most recent centred, older ones peek from the right
            out.append(Element("card", x, H / 2 - ch / 2, cw, ch, name, f"card:{name}", APP_COLORS[name]))
        out.reverse()  # most recent drawn last (on top)
        if not self.recents:
            out.append(Element("paragraph", 20, H / 2 - 20, W - 40, 40, "No Recent Apps"))
        return out

    def _prompt_elements(self) -> list[Element]:
        W, H = self.model.width_pt, self.model.height_pt
        return [Element("alert", W / 2 - 135, H / 2 - 80, 270, 160, "Allow accessory to connect?",
                        data={"buttons": ["Don't Allow", "Allow"]})]

    def _lock_layout(self) -> Layout:
        W = self.model.width_pt
        return Layout("lock", [
            Element("clock", 0, self.top + 60, W, 90, "9:41"),
            Element("paragraph", 0, self.top + 150, W, 30, "Monday, September 25"),
            Element("paragraph", 0, self.model.height_pt - 120, W, 30, "Unlock to continue"),
        ])
