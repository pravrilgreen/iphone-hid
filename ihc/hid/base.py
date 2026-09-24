from typing import Protocol, Sequence


class HidBackend(Protocol):
    def info(self) -> dict: ...
    def keyboard(self, modifiers: int, keys: Sequence[int]) -> None: ...
    def media(self, code: int) -> None: ...
    def mouse_rel(self, dx: int, dy: int, buttons: int = 0, wheel: int = 0) -> None: ...
    def close(self) -> None: ...


class HidError(Exception):
    """Base class for HID backend failures; messages are meant to be shown to the operator."""


class HidPortError(HidError):
    """The serial port could not be opened, read or written."""


class HidTimeout(HidError):
    """No valid reply in time. `received` holds the raw bytes that did arrive (garbage hints at baud)."""

    def __init__(self, message: str, received: bytes = b""):
        super().__init__(message)
        self.received = received


class HidStatusError(HidError):
    """The device answered with an error status."""

    def __init__(self, message: str, cmd: int, status: int | None):
        super().__init__(message)
        self.cmd = cmd
        self.status = status


class HidProtocolError(HidError):
    """The device answered with a frame of unexpected shape."""
