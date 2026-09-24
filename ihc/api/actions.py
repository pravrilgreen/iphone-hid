"""Action shapes shared by the REST endpoints, recorded scripts and the /control socket.

Every action is validated before it reaches a device (unknown fields, bad coordinates spaces,
untypeable text and unknown keys are rejected up front), then run as one blocking device call.
"""

from __future__ import annotations

import inspect
import time
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ..hid.protocol import MEDIA_KEYS
from ..input import keymap

Space = Literal["norm", "pt", "frame"]


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Empty(Body):
    pass


class Point(Body):
    x: float
    y: float
    space: Space = "norm"


class Tap(Point):
    long: bool = False


class Swipe(Body):
    x1: float
    y1: float
    x2: float
    y2: float
    space: Space = "norm"
    hold_end: float = Field(0.0, ge=0.0, le=30.0)
    duration: float | None = Field(None, gt=0.0, le=10.0)


class Scroll(Point):
    amount: int = Field(ge=-100, le=100)


class TypeText(Body):
    text: str = Field(max_length=10_000)

    @field_validator("text")
    @classmethod
    def _typeable(cls, v: str) -> str:
        keymap.text_reports(v)  # raises ValueError naming the characters a US layout cannot type
        return v


class Key(Body):
    combo: str = Field(min_length=1, max_length=100)

    @field_validator("combo")
    @classmethod
    def _known(cls, v: str) -> str:
        keymap.parse_combo(v)
        return v


class Media(Body):
    key: str

    @field_validator("key")
    @classmethod
    def _known(cls, v: str) -> str:
        if v not in MEDIA_KEYS:
            raise ValueError(f"unknown media key {v!r}; one of {', '.join(MEDIA_KEYS)}")
        return v


class OpenUrl(Body):
    url: str = Field(min_length=1, max_length=2000)

    @field_validator("url")
    @classmethod
    def _typeable(cls, v: str) -> str:
        keymap.text_reports(v)
        return v


class Wait(Body):
    seconds: float = Field(ge=0.0, le=300.0)


CALIBRATE_OPTIONS = {"coarse_counts", "fine_counts", "repeats", "validate", "page_timeout", "click_timeout", "seed"}


class Calibrate(Body):
    page_url: str | None = Field(None, max_length=2000)
    options: dict[str, Any] = Field(default_factory=dict)

    @field_validator("options")
    @classmethod
    def _known(cls, v: dict) -> dict:
        bad = sorted(set(v) - CALIBRATE_OPTIONS)
        if bad:
            raise ValueError(f"unknown calibration options {bad}; known: {sorted(CALIBRATE_OPTIONS)}")
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


# name -> (body model, runner(device, body)); runners block and may raise device errors
ACTIONS: dict[str, tuple[type[Body], Callable[[Any, Any], Any]]] = {
    "tap": (Tap, lambda d, a: d.tap(a.x, a.y, space=a.space, long=a.long)),
    "long_press": (Point, lambda d, a: d.tap(a.x, a.y, space=a.space, long=True)),
    "move": (Point, lambda d, a: d.move(a.x, a.y, space=a.space)),
    "swipe": (Swipe, _swipe),
    "scroll": (Scroll, lambda d, a: d.scroll(a.x, a.y, a.amount, space=a.space)),
    "type": (TypeText, lambda d, a: d.type(a.text)),
    "key": (Key, lambda d, a: d.key(a.combo)),
    "home": (Empty, lambda d, a: d.home()),
    "app_switcher": (Empty, lambda d, a: d.app_switcher()),
    "media": (Media, lambda d, a: d.media(a.key)),
    "open_url": (OpenUrl, lambda d, a: d.open_url(a.url)),
    "release_all": (Empty, _release),
}
SCRIPT_ONLY = {"wait": (Wait, _wait)}


def parse(kind: str, params: dict, *, script: bool = False) -> tuple[Callable[[Any, Any], Any], Body]:
    """Validate one action; raises ValueError (a pydantic ValidationError is one) on bad input."""
    table = {**ACTIONS, **SCRIPT_ONLY} if script else ACTIONS
    if kind not in table:
        raise ValueError(f"unknown action {kind!r}; one of {', '.join(table)}")
    model, run = table[kind]
    return run, model.model_validate(params)


def describe(exc: Exception) -> str:
    """One readable line for a validation error."""
    if isinstance(exc, ValidationError):
        parts = []
        for err in exc.errors():
            loc = ".".join(str(p) for p in err.get("loc", ()) if p != "body")
            msg = err.get("msg", "invalid").removeprefix("Value error, ")
            parts.append(f"{loc}: {msg}" if loc else msg)
        return "; ".join(parts)
    return str(exc)
