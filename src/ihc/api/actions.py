"""Action shapes shared by the REST endpoints, recorded scripts and the /control socket.

Every action is validated before it reaches a device (unknown fields, bad coordinate spaces,
untypeable text and unknown keys are rejected up front), then run as one blocking device call.
"""

from __future__ import annotations

import inspect
import time
from dataclasses import dataclass
from typing import Annotated, Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from ..hid.protocol import MEDIA_KEYS
from ..input import keymap

Space = Literal["norm", "pt", "frame"]
SPACE_DOC = ('Coordinate space: "norm" = 0..1 across the phone screen (default), "pt" = iOS points, '
             '"frame" = pixels of the captured video frame')


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _on_screen(self):
        """Normalized coordinates must lie on the screen (other spaces are checked by the device)."""
        if getattr(self, "space", None) == "norm":
            for name in ("x", "y", "x1", "y1", "x2", "y2"):
                v = getattr(self, name, None)
                if v is not None and not 0.0 <= v <= 1.0:
                    raise ValueError(f"{name} = {v} is off the screen: normalized coordinates are 0..1")
        return self


class Empty(Body):
    """No parameters."""


class Point(Body):
    """A point on the screen."""

    x: float = Field(description="Horizontal coordinate (0 = left edge in norm space)", examples=[0.5])
    y: float = Field(description="Vertical coordinate (0 = top edge in norm space)", examples=[0.5])
    space: Space = Field("norm", description=SPACE_DOC)


class Tap(Point):
    """Tap at a point; `long` holds it (0.8 s) for a long press."""

    long: bool = Field(False, description="Hold instead of tapping")


class Swipe(Body):
    """Press at (x1, y1), drag to (x2, y2), release."""

    x1: float = Field(examples=[0.5])
    y1: float = Field(examples=[0.8])
    x2: float = Field(examples=[0.5])
    y2: float = Field(examples=[0.3])
    space: Space = Field("norm", description=SPACE_DOC)
    hold_end: float = Field(0.0, ge=0.0, le=30.0, description="Seconds to hold at the end before lifting "
                                                             "(> 0 turns a fling into a precise drag)")
    duration: float | None = Field(None, gt=0.0, le=10.0, description="Drag duration in seconds (absolute-pointer "
                                                                      "phones; relative drags take their calibrated time)")


class Scroll(Point):
    """Scroll the wheel at a point."""

    amount: int = Field(ge=-100, le=100, description="Wheel detents; positive scrolls up", examples=[-3])


class TypeText(Body):
    """Type text on the phone's hardware keyboard (US layout: printable ASCII, \\n and \\t)."""

    text: str = Field(max_length=10_000, examples=["hello world\n"])

    @field_validator("text")
    @classmethod
    def _typeable(cls, v: str) -> str:
        keymap.text_reports(v)  # raises ValueError naming the characters a US layout cannot type
        return v


class Key(Body):
    """Press a key combination."""

    combo: str = Field(min_length=1, max_length=100, description='Modifiers and keys joined by "+", e.g. '
                                                                 '"cmd+space", "esc", "shift+tab", "cmd+h"',
                       examples=["cmd+space"])

    @field_validator("combo")
    @classmethod
    def _known(cls, v: str) -> str:
        keymap.parse_combo(v)
        return v


class Media(Body):
    """Press a media key."""

    key: str = Field(description="One of: " + ", ".join(MEDIA_KEYS), examples=["volume_up"])

    @field_validator("key")
    @classmethod
    def _known(cls, v: str) -> str:
        if v not in MEDIA_KEYS:
            raise ValueError(f"unknown media key {v!r}; one of {', '.join(MEDIA_KEYS)}")
        return v


class OpenUrl(Body):
    """Open a URL in Safari through Spotlight."""

    url: str = Field(min_length=1, max_length=2000, examples=["https://example.com"])

    @field_validator("url")
    @classmethod
    def _typeable(cls, v: str) -> str:
        keymap.text_reports(v)
        return v


class Wait(Body):
    """Pause a script."""

    seconds: float = Field(ge=0.0, le=300.0, examples=[0.5])


Counts = Annotated[list[Annotated[int, Field(ge=1, le=127)]], Field(min_length=2, max_length=16)]


class CalibrateOptions(BaseModel):
    """Advanced ihc.calibration.calibrate() options, checked (types and ranges) before the phone is
    touched; omitted ones keep the calibration's defaults."""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    validate_: Annotated[int, Field(ge=3, le=100)] | None = Field(
        None, alias="validate", description="Validation moves after the measurement (default 8)")
    max_error: Annotated[float, Field(ge=0.5, le=10.0)] | None = Field(
        None, description="Largest validation landing error accepted, points (default 3)")
    repeats: Annotated[int, Field(ge=1, le=5)] | None = Field(None, description="Repeats of each measured run")
    page_timeout: Annotated[float, Field(ge=1.0, le=120.0)] | None = Field(
        None, description="Seconds to wait for the page to load on the phone (default 15)")
    click_timeout: Annotated[float, Field(ge=0.1, le=10.0)] | None = Field(
        None, description="How late a page event may be, seconds (default 2)")
    coarse_target: Annotated[float, Field(ge=5.0, le=40.0)] | None = Field(
        None, description="Size of a coarse step, points (default 20)")
    try_absolute: bool | None = Field(None, description="Test whether the phone follows absolute reports first "
                                                        "(default true; false forces relative mode)")
    coarse_counts: Counts | None = Field(None, description="Run lengths (reports) of the coarse measurement")
    fine_counts: Counts | None = Field(None, description="Run lengths (reports) of the fine measurement")
    seed: Annotated[int, Field(ge=0, le=2**32 - 1)] | None = None

    @model_validator(mode="before")
    @classmethod
    def _known(cls, v):
        if isinstance(v, dict):
            known = sorted(f.alias or name for name, f in cls.model_fields.items())
            bad = sorted(set(v) - set(known))
            if bad:
                raise ValueError(f"unknown calibration options {bad}; known: {known}")
        return v

    def kwargs(self) -> dict[str, Any]:
        """The options given, as calibrate() keyword arguments."""
        out = self.model_dump(by_alias=True, exclude_none=True)
        return {k: tuple(v) if isinstance(v, list) else v for k, v in out.items()}


CALIBRATE_OPTIONS = {f.alias or name for name, f in CalibrateOptions.model_fields.items()}


class PointerMode(Body):
    """How the pointer is driven until (or instead of) a calibration."""

    mode: Literal["absolute", "relative"] = Field(..., description="absolute: one report places the pointer (the "
                                                                  "whole 0..32767 range over the whole screen); "
                                                                  "relative: planned runs of relative reports")


class Calibrate(Body):
    """Calibrate the pointer with the Safari calibration page."""

    page_url: str | None = Field(None, max_length=2000, description="URL of the calibration page as the phone "
                                                                    "reaches it (default <public_url>/calibrate/<id>); "
                                                                    "the server adds the page's key `k` to it")
    open_page: bool = Field(True, description="open the page through Spotlight first; false: it is already open "
                                              "in Safari (opened by hand, from the page_url of GET .../calibration)")
    options: CalibrateOptions = Field(default_factory=CalibrateOptions,
                                      description="Advanced ihc.calibration.calibrate() options: "
                                      + ", ".join(sorted(CALIBRATE_OPTIONS)))

    @field_validator("page_url")
    @classmethod
    def _typeable(cls, v: str | None) -> str | None:
        if v is not None:
            if not v.lower().startswith(("http://", "https://")):
                raise ValueError("page_url must be an http:// or https:// URL")
            keymap.text_reports(v)  # typed through Spotlight
        return v


def _swipe(d, a: Swipe):
    kwargs = {"space": a.space, "hold_end": a.hold_end}
    if a.duration is not None and "duration" in inspect.signature(d.swipe).parameters:
        kwargs["duration"] = a.duration
    return d.swipe(a.x1, a.y1, a.x2, a.y2, **kwargs)


def _wait(d, a: Wait):
    time.sleep(a.seconds)
    return {"waited": a.seconds}


def _release(d, a: Empty):
    d.release_all()
    return {"released": True}


@dataclass(frozen=True)
class Spec:
    model: type[Body]
    run: Callable[[Any, Any], Any]  # (device, body) -> result; blocks, may raise device errors
    summary: str
    description: str = ""


ACTIONS: dict[str, Spec] = {
    "tap": Spec(Tap, lambda d, a: d.tap(a.x, a.y, space=a.space, long=a.long), "Tap",
                "Move the pointer to the point (anchored, paced relative moves or one absolute report) and click."),
    "long_press": Spec(Point, lambda d, a: d.tap(a.x, a.y, space=a.space, long=True), "Long press",
                       "Like tap, holding the button 0.8 s."),
    "move": Spec(Point, lambda d, a: d.move(a.x, a.y, space=a.space), "Move the pointer",
                 "Move the pointer to the point without clicking."),
    "swipe": Spec(Swipe, _swipe, "Swipe / drag", "Press at the start point, drag to the end point, release."),
    "scroll": Spec(Scroll, lambda d, a: d.scroll(a.x, a.y, a.amount, space=a.space), "Scroll",
                   "Move to the point, then turn the wheel `amount` detents (positive = up)."),
    "type": Spec(TypeText, lambda d, a: d.type(a.text), "Type text",
                 "Type on the hardware keyboard (US layout). Rejected before anything is sent if a character "
                 "cannot be typed."),
    "key": Spec(Key, lambda d, a: d.key(a.combo), "Key combination", 'E.g. "cmd+space" (Spotlight), "esc", "cmd+h".'),
    "home": Spec(Empty, lambda d, a: d.home(), "Home", "Secondary mouse button, mapped to Home in AssistiveTouch."),
    "app_switcher": Spec(Empty, lambda d, a: d.app_switcher(), "App Switcher",
                         "Middle mouse button, mapped to App Switcher in AssistiveTouch."),
    "media": Spec(Media, lambda d, a: d.media(a.key), "Media key", "Volume, play/pause..."),
    "open_url": Spec(OpenUrl, lambda d, a: d.open_url(a.url), "Open a URL", "Spotlight, type the URL, Return."),
    "release_all": Spec(Empty, _release, "Release everything", "Release every key and mouse button."),
}
SCRIPT_ONLY: dict[str, Spec] = {"wait": Spec(Wait, _wait, "Wait")}


def parse(kind: str, params: dict, *, script: bool = False) -> tuple[Callable[[Any, Any], Any], Body]:
    """Validate one action; raises ValueError (a pydantic ValidationError is one) on bad input."""
    table = {**ACTIONS, **SCRIPT_ONLY} if script else ACTIONS
    if kind not in table:
        raise ValueError(f"unknown action {kind!r}; one of {', '.join(table)}")
    spec = table[kind]
    return spec.run, spec.model.model_validate(params)


def describe(exc: Exception) -> str:
    """One readable line for a validation error."""
    if isinstance(exc, ValidationError):
        return "; ".join(describe_error(e) for e in exc.errors())
    return str(exc)


def describe_error(err: dict) -> str:
    """'field: message' for one pydantic/FastAPI error entry."""
    loc = [str(p) for p in err.get("loc", ()) if p not in ("body", "query", "path")]
    msg = str(err.get("msg", "invalid")).removeprefix("Value error, ")
    if err.get("type") == "missing" and not loc:
        return "a JSON request body is required"
    return f"{'.'.join(loc)}: {msg}" if loc else msg
