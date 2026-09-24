"""CH9329 serial frame codec.

Checked against WCH "CH9329芯片串口通信协议" V1.0 (the protocol PDF shipped in CH9329EVT.ZIP): frame
layout, command codes, status codes and the example frames in tests/test_protocol.py all come from
it. Hardware behaviour the document leaves open is listed in docs/ch9329-protocol.md.

Frame: 57 AB | ADDR | CMD | LEN | DATA[LEN] | SUM, where SUM = sum of all prior bytes & 0xFF.
Replies echo the command as CMD | 0x80 (success) or CMD | 0xC0 (error, DATA = 1 status byte).
"""

from dataclasses import dataclass
from enum import IntEnum
from typing import Sequence

HEADER = b"\x57\xab"
DEFAULT_ADDR = 0x00
# Broadcast: accepted by every chip, never answered.
BROADCAST_ADDR = 0xFF
MAX_DATA_LEN = 64
RESPONSE_OK_FLAG = 0x80
RESPONSE_ERR_FLAG = 0xC0
# The doc tells the host to treat "no valid reply within 500 ms" as a failed command.
REPLY_TIMEOUT_S = 0.5
# Sent by the chip on its own in custom-HID mode (data the USB host wrote); never a reply.
CHIP_HID_DATA = 0x87

# ESP32 bridge extensions (firmware/esp32_ble_hid), not in the WCH protocol: a real CH9329 answers
# E3 to the vendor command. The bridge reports GET_INFO version 0x40 and fills the reserved bytes:
# byte 3 output link, byte 4 HID collections, byte 5 vendor features.
BRIDGE_VERSION = 0x40
CMD_MS_REL_RUN = 0x30
FEATURE_REL_RUN = 0x01
FEATURE_REL_RUN_QUARTER_MS = 0x02  # the run interval may be given in 0.25 ms units
FEATURE_REL_RUN_LATE = 0x04  # a run played off schedule answers RUN_LATE (0xE7)
REL_RUN_QUARTER_FLAG = 0x80  # in the run's flags byte (bits 0-2: buttons)
REL_RUN_MAX_MS = 2000  # (count - 1) * interval of one run
BRIDGE_OUTPUTS = {0x01: "ble", 0x02: "usb", 0x7F: "simulator"}
BRIDGE_COLLECTIONS = {0x01: "keyboard", 0x02: "mouse", 0x04: "consumer", 0x08: "system", 0x10: "absolute"}


class Cmd(IntEnum):
    GET_INFO = 0x01
    SEND_KB_GENERAL_DATA = 0x02
    SEND_KB_MEDIA_DATA = 0x03
    SEND_MS_ABS_DATA = 0x04
    SEND_MS_REL_DATA = 0x05
    SEND_MY_HID_DATA = 0x06
    GET_PARA_CFG = 0x08
    SET_PARA_CFG = 0x09
    GET_USB_STRING = 0x0A
    SET_USB_STRING = 0x0B
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
    RUN_LATE = 0xE7  # ESP32 bridge only (vendor): a timed run played, but off its schedule


STATUS_TEXT = {
    Status.OK: "success",
    Status.TIMEOUT: "chip timed out receiving a byte",
    Status.BAD_HEADER: "chip saw a bad frame header",
    Status.BAD_CMD: "unknown command code",
    Status.BAD_CHECKSUM: "checksum mismatch",
    Status.BAD_PARAM: "invalid parameter",
    Status.EXEC_ERROR: "frame OK but execution failed",
    Status.RUN_LATE: "run delivered, but a report left more than the bridge's threshold after its slot",
}

MOUSE_LEFT = 0x01
MOUSE_RIGHT = 0x02
MOUSE_MIDDLE = 0x04

# SEND_KB_MEDIA_DATA carries either an ACPI report (id 1, 1 bitmap byte) or a multimedia report
# (id 2, 3 bitmap bytes). Bit n of these masks is bit n%8 of bitmap byte n//8 (appendix 1 table 2).
ACPI_REPORT_ID = 0x01
MEDIA_REPORT_ID = 0x02
ACPI_KEYS = {"power": 0x01, "sleep": 0x02, "wake": 0x04}
MEDIA_KEYS = {
    name: 1 << bit
    for bit, name in enumerate(
        [
            "volume_up", "volume_down", "mute", "play_pause",
            "next_track", "prev_track", "cd_stop", "eject",
            "email", "www_search", "www_favorites", "www_home",
            "www_back", "www_forward", "www_stop", "refresh",
            "media", "explorer", "calculator", "screen_save",
            "my_computer", "minimize", "record", "rewind",
        ]
    )
}


class FrameError(ValueError):
    pass


def checksum(data: bytes) -> int:
    return sum(data) & 0xFF


def encode(cmd: int, data: bytes = b"", addr: int = DEFAULT_ADDR) -> bytes:
    if len(data) > MAX_DATA_LEN:
        raise FrameError(f"payload too long: {len(data)} bytes (max {MAX_DATA_LEN})")
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

    @property
    def status(self) -> int | None:
        """Status byte of a 1-byte reply (every HID/config write and every error reply)."""
        return self.data[0] if len(self.data) == 1 else None

    def to_bytes(self) -> bytes:
        return encode(self.cmd, self.data, self.addr)


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
    """Incremental parser: feed raw serial bytes, get complete frames; skips garbage before a header.

    `discarded` counts bytes thrown away as garbage, which is the main symptom of a baud mismatch.
    """

    def __init__(self) -> None:
        self._buf = bytearray()
        self.discarded = 0

    @property
    def pending(self) -> int:
        """Bytes held back waiting for the rest of a frame."""
        return len(self._buf)

    def reset(self) -> None:
        self._buf.clear()

    def feed(self, chunk: bytes) -> list[Frame]:
        self._buf += chunk
        frames = []
        while True:
            start = self._buf.find(HEADER)
            if start < 0:
                # keep a trailing 0x57 in case the header is split across chunks
                keep = 1 if self._buf[-1:] == HEADER[:1] else 0
                self._drop(len(self._buf) - keep)
                return frames
            self._drop(start)
            if len(self._buf) < 5:
                return frames
            if self._buf[4] > MAX_DATA_LEN:
                self._drop(2)  # impossible LEN: false header
                continue
            total = 6 + self._buf[4]
            if len(self._buf) < total:
                return frames
            raw = bytes(self._buf[:total])
            try:
                frames.append(decode(raw))
                del self._buf[:total]
            except FrameError:
                # resync past this false header
                self._drop(2)

    def _drop(self, n: int) -> None:
        self.discarded += n
        del self._buf[:n]


def _i8(v: int) -> int:
    if not -127 <= v <= 127:
        raise FrameError(f"value out of int8 range: {v}")
    return v & 0xFF


def kb_general(modifiers: int, keys: Sequence[int], addr: int = DEFAULT_ADDR) -> bytes:
    if len(keys) > 6:
        raise FrameError("at most 6 simultaneous keys")
    padded = list(keys) + [0] * (6 - len(keys))
    return encode(Cmd.SEND_KB_GENERAL_DATA, bytes([modifiers & 0xFF, 0x00, *padded]), addr)


def kb_media(bits: int, addr: int = DEFAULT_ADDR) -> bytes:
    """Multimedia keys held down, as a MEDIA_KEYS bitmask; 0 releases all."""
    if not 0 <= bits <= 0xFFFFFF:
        raise FrameError(f"media bitmap out of range: {bits:#x}")
    return encode(Cmd.SEND_KB_MEDIA_DATA, bytes([MEDIA_REPORT_ID, *bits.to_bytes(3, "little")]), addr)


def kb_acpi(bits: int, addr: int = DEFAULT_ADDR) -> bytes:
    """ACPI keys held down, as an ACPI_KEYS bitmask; 0 releases all."""
    if not 0 <= bits <= 0x07:
        raise FrameError(f"ACPI bitmap out of range: {bits:#x}")
    return encode(Cmd.SEND_KB_MEDIA_DATA, bytes([ACPI_REPORT_ID, bits]), addr)


def mouse_rel(dx: int, dy: int, buttons: int = 0, wheel: int = 0, addr: int = DEFAULT_ADDR) -> bytes:
    """Positive dx = right, positive dy = down, positive wheel = scroll up (per the WCH doc)."""
    return encode(Cmd.SEND_MS_REL_DATA, bytes([0x01, buttons & 0x07, _i8(dx), _i8(dy), _i8(wheel)]), addr)


def mouse_abs(x: int, y: int, buttons: int = 0, wheel: int = 0, addr: int = DEFAULT_ADDR) -> bytes:
    """Absolute position on the chip's 4096 x 4096 grid (X/Y little-endian)."""
    for name, v in (("x", x), ("y", y)):
        if not 0 <= v <= 4095:
            raise FrameError(f"absolute {name} out of range 0..4095: {v}")
    data = bytes([0x02, buttons & 0x07, *x.to_bytes(2, "little"), *y.to_bytes(2, "little"), _i8(wheel)])
    return encode(Cmd.SEND_MS_ABS_DATA, data, addr)


def mouse_rel_run(dx: int, dy: int, count: int, interval_ms: float, buttons: int = 0, addr: int = DEFAULT_ADDR,
                  quarter: bool = False) -> bytes:
    """Bridge only: `count` relative reports of (dx, dy), one every `interval_ms`, timed by the
    bridge. A run owns `count` slots and replies at the end of the last one, so runs queued back to
    back play as one fixed-rate sequence. With `quarter` the interval goes in 0.25 ms units (a
    bridge that advertises FEATURE_REL_RUN_QUARTER_MS), up to 63.75 ms; otherwise whole ms."""
    if not 1 <= count <= 255:
        raise FrameError(f"run count out of range 1..255: {count}")
    units = interval_ms * 4 if quarter else interval_ms
    if abs(units - round(units)) > 1e-6:
        raise FrameError(f"run interval {interval_ms} ms is not a whole number of {'0.25 ms' if quarter else 'ms'}")
    units = round(units)
    if not 0 <= units <= 255:
        raise FrameError(f"run interval out of range: {interval_ms} ms")
    if (count - 1) * interval_ms > REL_RUN_MAX_MS:
        raise FrameError(f"run too long: {count} reports every {interval_ms} ms")
    flags = (buttons & 0x07) | (REL_RUN_QUARTER_FLAG if quarter else 0)
    return encode(CMD_MS_REL_RUN, bytes([_i8(dx), _i8(dy), count, units, flags]), addr)


def get_info(addr: int = DEFAULT_ADDR) -> bytes:
    return encode(Cmd.GET_INFO, b"", addr)
