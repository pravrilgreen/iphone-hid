"""CH9329 serial frame codec.

UNVERIFIED: frame layout, command codes and status codes come from docs/handoff.md section 7 and
must be checked against the WCH CH9329 serial protocol document before relying on them.

Frame: 57 AB | ADDR | CMD | LEN | DATA[LEN] | SUM, where SUM = sum of all prior bytes & 0xFF.
"""

from dataclasses import dataclass
from enum import IntEnum
from typing import Sequence

HEADER = b"\x57\xab"
DEFAULT_ADDR = 0x00
RESPONSE_OK_FLAG = 0x80
RESPONSE_ERR_FLAG = 0xC0


class Cmd(IntEnum):
    GET_INFO = 0x01
    SEND_KB_GENERAL_DATA = 0x02
    SEND_KB_MEDIA_DATA = 0x03
    SEND_MS_ABS_DATA = 0x04
    SEND_MS_REL_DATA = 0x05
    GET_PARA_CFG = 0x08
    SET_PARA_CFG = 0x09
    SET_DEFAULT_CFG = 0x0C
    RESET = 0x0F


class Status(IntEnum):
    OK = 0x00
    TIMEOUT = 0xE1
    BAD_HEADER = 0xE2
    BAD_CMD = 0xE3
    BAD_CHECKSUM = 0xE4
    BAD_PARAM = 0xE5
    EXEC_ERROR = 0xE6


class FrameError(ValueError):
    pass


def checksum(data: bytes) -> int:
    return sum(data) & 0xFF


def encode(cmd: int, data: bytes = b"", addr: int = DEFAULT_ADDR) -> bytes:
    if len(data) > 0xFF:
        raise FrameError(f"payload too long: {len(data)} bytes")
    body = HEADER + bytes([addr, cmd, len(data)]) + data
    return body + bytes([checksum(body)])


@dataclass(frozen=True)
class Frame:
    addr: int
    cmd: int
    data: bytes

    @property
    def is_error(self) -> bool:
        return self.cmd & RESPONSE_ERR_FLAG == RESPONSE_ERR_FLAG

    @property
    def request_cmd(self) -> int:
        return self.cmd & 0x3F


def decode(frame: bytes) -> Frame:
    if len(frame) < 6:
        raise FrameError(f"frame too short: {frame.hex(' ')}")
    if frame[:2] != HEADER:
        raise FrameError(f"bad header: {frame[:2].hex(' ')}")
    length = frame[4]
    if len(frame) != 6 + length:
        raise FrameError(f"length mismatch: LEN={length}, got {len(frame)} bytes")
    if checksum(frame[:-1]) != frame[-1]:
        raise FrameError(f"bad checksum: {frame.hex(' ')}")
    return Frame(addr=frame[2], cmd=frame[3], data=bytes(frame[5:-1]))


class FrameParser:
    """Incremental parser: feed raw serial bytes, get complete frames; skips garbage before a header."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, chunk: bytes) -> list[Frame]:
        self._buf += chunk
        frames = []
        while True:
            start = self._buf.find(HEADER)
            if start < 0:
                # keep a trailing 0x57 in case the header is split across chunks
                del self._buf[: max(0, len(self._buf) - 1)]
                return frames
            del self._buf[:start]
            if len(self._buf) < 5:
                return frames
            total = 6 + self._buf[4]
            if len(self._buf) < total:
                return frames
            raw = bytes(self._buf[:total])
            try:
                frames.append(decode(raw))
                del self._buf[:total]
            except FrameError:
                # resync past this false header
                del self._buf[:2]


def _i8(v: int) -> int:
    if not -127 <= v <= 127:
        raise FrameError(f"value out of int8 range: {v}")
    return v & 0xFF


def kb_general(modifiers: int, keys: Sequence[int], addr: int = DEFAULT_ADDR) -> bytes:
    if len(keys) > 6:
        raise FrameError("at most 6 simultaneous keys")
    padded = list(keys) + [0] * (6 - len(keys))
    return encode(Cmd.SEND_KB_GENERAL_DATA, bytes([modifiers & 0xFF, 0x00, *padded]), addr)


def mouse_rel(dx: int, dy: int, buttons: int = 0, wheel: int = 0, addr: int = DEFAULT_ADDR) -> bytes:
    return encode(Cmd.SEND_MS_REL_DATA, bytes([0x01, buttons & 0x07, _i8(dx), _i8(dy), _i8(wheel)]), addr)


def get_info(addr: int = DEFAULT_ADDR) -> bytes:
    return encode(Cmd.GET_INFO, b"", addr)
