"""Response and request models of the HTTP API (validation and the /docs reference).

Device status models accept fields they do not declare, so the API passes on whatever
IPhoneDevice.status() reports.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator

ErrorCode = Literal["not_found", "invalid_input", "device_error", "no_video", "hid_timeout", "internal",
                    "bad_request", "unauthorized", "forbidden", "too_large", "too_many", "client_gone"]
CODES: dict[int, ErrorCode] = {400: "bad_request", 401: "unauthorized", 403: "forbidden", 404: "not_found",
                               405: "invalid_input", 413: "too_large", 415: "invalid_input", 422: "invalid_input",
                               409: "device_error", 429: "too_many", 499: "client_gone", 503: "no_video",
                               504: "hid_timeout", 500: "internal"}


class _Open(BaseModel):
    model_config = ConfigDict(extra="allow")


class ErrorResponse(BaseModel):
    """Every error has this shape."""

    error: str = Field(description="What went wrong, readable by an operator", examples=["no device 'iphone-09'"])
    code: ErrorCode = Field(description="404 not_found, 422 invalid_input, 409 device_error (HID, pointer, "
                                        "calibration, locked phone...), 503 no_video, 504 hid_timeout, 500 internal; "
                                        "400 bad_request (Host not allowed), 401 unauthorized (token), 403 forbidden "
                                        "(Origin, calibration key), 413 too_large, 415 invalid_input (not JSON), "
                                        "429 too_many (viewers)")


ERRORS = {
    401: {"model": ErrorResponse, "description": "Missing or wrong API token (when the server has one)"},
    404: {"model": ErrorResponse, "description": "Unknown device"},
    409: {"model": ErrorResponse, "description": "The device failed or refused the action (HID error, phone "
                                                 "locked / accessory not allowed, pointer or calibration error)"},
    422: {"model": ErrorResponse, "description": "Invalid input; nothing was sent to the phone"},
    504: {"model": ErrorResponse, "description": "The HID chip did not answer in time"},
}


class Health(BaseModel):
    ok: bool = True
    devices: int = Field(description="Number of devices on this host")
    uptime_s: float
    public_url: str | None = Field(None, description="How phones reach this server (calibration page)")
    version: str = ""


class DeviceHealth(_Open):
    hid: bool | None = Field(None, description="Chip answers on its serial port")
    usb_connected: bool | None = Field(None, description="The phone enumerated the chip (false: locked phone, "
                                                         "accessory prompt, cable)")
    signal: bool | None = Field(None, description="Video frames present and not black")
    recalibrate: str | None = Field(None, description="Why the pointer calibration no longer holds (e.g. a "
                                                      "Bluetooth link renegotiated its report period); null: it holds")
    error: str | None = None


class Rect(_Open):
    x: float
    y: float
    w: float
    h: float
    orientation: str | None = None


class Screen(_Open):
    points: list[float] | None = Field(None, description="Screen size in iOS points [w, h]")
    rect: Rect | None = Field(None, description="Where the screen lies in the video frame (pixels)")


class Pointer(_Open):
    mode: str = Field("relative", description='"relative" (AssistiveTouch pointer, anchored paced moves) or '
                                              '"absolute" (the phone follows absolute reports)')
    pt: list[float] | None = Field(None, description="Estimated position in points (null: unknown)")
    norm: list[float] | None = None
    reports: int | None = None
    resends: int | None = None


class Calibration(_Open):
    calibrated: bool | None = None
    method: str | None = Field(None, description='"guess" (not calibrated), "safari", "sim", ...')
    measured_at: str | None = None
    validation: dict[str, Any] | None = Field(None, description="Landing error of the validation moves, in points "
                                                                "(mean, max, n)")


class DeviceStatus(_Open):
    id: str
    model: str | None = None
    kind: str | None = Field(None, description='"hardware" or "sim"')
    state: str = Field(description="ready | busy | hid_disconnected | hid_offline | no_signal | needs_calibration "
                                   "(taps would land off: see health.recalibrate)")
    busy_with: str | None = Field(None, description="Action running now")
    health: DeviceHealth | None = None
    screen: Screen | None = None
    pointer: Pointer | None = None
    calibration: Calibration | None = None
    hid: dict[str, Any] | None = Field(None, description="Serial port, baud rate and frame counters")
    video: dict[str, Any] | None = Field(None, description="Capture statistics")
    counters: dict[str, Any] | None = None
    last_result: dict[str, Any] | None = Field(None, description="Result of the last action")


class DeviceList(BaseModel):
    devices: list[DeviceStatus]


class CalibrationSummary(Calibration):
    mode: str = Field("relative", description="Pointer mode")
    page_url: str | None = Field(None, description="The calibration page for this device, with its current key "
                                                   "`k` (valid until the next calibration ends): open it in Safari "
                                                   "by hand for a calibration with open_page=false")


class ActionResponse(BaseModel):
    ok: Literal[True] = True
    result: Any = Field(None, description="The device's report: action, target in points, pointer plan, HID reports "
                                          "sent, duration in seconds")


class ScriptStep(_Open):
    """{"type": <action>, ...its parameters}; "wait" takes {"seconds"}."""

    type: str = Field(description="tap, long_press, move, swipe, scroll, type, key, home, app_switcher, media, "
                                  "open_url, release_all or wait", examples=["tap"])


class ScriptRequest(BaseModel):
    actions: list[ScriptStep] = Field(max_length=10_000, examples=[[
        {"type": "home"}, {"type": "wait", "seconds": 0.5}, {"type": "tap", "x": 0.5, "y": 0.3},
        {"type": "type", "text": "hello\n"}]])
    stop_on_error: bool = Field(True, description="Stop at the first failing step")


class StepResult(BaseModel):
    index: int
    type: str
    ok: bool
    result: Any = None
    error: str | None = None
    status: int | None = Field(None, description="HTTP status the step's error maps to")
    seconds: float


class ScriptResult(BaseModel):
    steps: list[StepResult]
    completed: int = Field(description="Steps that succeeded")
    total: int
    seconds: float


class ScriptResponse(BaseModel):
    ok: bool = Field(description="Every step that ran succeeded")
    error: str | None = Field(None, description="First failing step")
    result: ScriptResult


class CalibrationEvent(BaseModel):
    """An event of the calibration page (web/calibrate.html). Fields not listed here are dropped."""

    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)

    type: Literal["hello", "click", "move"]
    pid: str | None = Field(None, max_length=64, description="Random id of one page load")
    seq: int | None = Field(None, ge=0, description="Event number within one page load, from 1")
    t: float | None = Field(None, description="performance.now() on the phone when the event happened, ms")
    x: float | None = Field(None, description="clientX in CSS px (= points), click and move")
    y: float | None = None
    button: int | None = Field(None, ge=0, le=31)
    screen_w: float | None = Field(None, description="hello: screen.width")
    screen_h: float | None = None
    inner_w: float | None = Field(None, description="hello: innerWidth")
    inner_h: float | None = None
    heartbeat_ms: int | None = Field(None, ge=0, le=600_000, description="hello: the page repeats hello this often "
                                                                         "(ms) while visible")
    dpr: float | None = None
    ua: str | None = None

    @field_validator("ua", mode="before")
    @classmethod
    def _short(cls, v):
        return v[:512] if isinstance(v, str) else v


CalibrationEvents = Union[Annotated[list[CalibrationEvent], Field(max_length=500)], CalibrationEvent]


class SimResult(BaseModel):
    state: str
    health: dict[str, Any] | None = None
