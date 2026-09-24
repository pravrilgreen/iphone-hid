"""Assemble a simulated phone setup: chip on a pty + phone UI + HDMI capture, like one real rig."""

from __future__ import annotations

from dataclasses import dataclass

from ..hid.fake import FakeBackend, FakeChip, SimPointer
from ..input.pointer import DirectionModel, PointerCalibration, RunModel
from ..models import get_model
from .hdmi import SimHdmiCapture
from .phone import SimPhone


@dataclass
class SimRig:
    id: str
    chip: FakeChip
    hid: FakeBackend
    phone: SimPhone
    capture: SimHdmiCapture

    def close(self) -> None:
        self.capture.close()
        self.hid.close()


def make_rig(
    id: str = "sim-1",
    model: str = "iphone-15",
    *,
    simulate_timing: bool = True,
    timeout: float = 0.5,
    tracking: float = 1.0,
    dark: bool = False,
    open_delay: float = 0.35,
    capture_size: tuple[int, int] = (1920, 1080),
    fps: float = 30.0,
    latency: float = 0.08,
    pointer_visible: bool = True,
    absolute: bool = False,
    trace=None,
) -> SimRig:
    """One simulated iPhone behind the real driver stack: HID frames go through the CH9329 driver
    over a pty (at 9600 baud timing when `simulate_timing`), video comes out as MJPEG frames."""
    chip = FakeChip()
    chip.pointer.tracking = tracking
    chip.pointer.absolute = absolute
    phone = SimPhone(get_model(model), dark=dark, open_delay=open_delay)
    phone.attach(chip)
    hid = FakeBackend(chip, simulate_timing=simulate_timing, timeout=timeout, trace=trace)
    capture = SimHdmiCapture(phone, chip.pointer, size=capture_size, fps=fps, latency=latency,
                             pointer_visible=pointer_visible)
    return SimRig(id, chip, hid, phone, capture)


def exact_calibration(pointer: SimPointer, base: PointerCalibration | None = None) -> PointerCalibration:
    """The calibration a perfect measurement would give for this simulated pointer (for tests and
    demos that should not spend time calibrating). Mirrors SimPointer's gain formula: the first
    report of a run arrives `rest` after the previous one, the others `interval` apart."""
    cal = base or PointerCalibration()

    def gain(units: int, dt: float) -> float:
        speed = units / max(dt, 0.008)
        return units * pointer.gain * pointer.tracking * (1 + pointer.accel * speed / 1000)

    def run(units: int) -> RunModel:
        a = gain(units, cal.interval)
        return RunModel(a, gain(units, min(cal.rest, pointer.idle_reset)) - a)

    d = DirectionModel(run(cal.step), run(cal.fine_step))
    absolute = pointer.absolute
    return PointerCalibration(
        mode="absolute" if absolute else "relative",
        abs_map=(4095 / pointer.width, 0.0, 4095 / pointer.height, 0.0) if absolute else None,
        abs_settle=pointer.abs_glide + 0.02,
        screen_pt=(pointer.width, pointer.height), step=cal.step, fine_step=cal.fine_step,
        interval=cal.interval, rest=cal.rest, reset_reports=6,
        edges=(0.0, 0.0, 1.0, 1.0),  # SimPointer clamps to [0, size - 1]
        right=d, left=d, down=d, up=d, method="sim",
    )
