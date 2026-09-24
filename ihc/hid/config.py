"""CH9329 50-byte parameter block (CMD_GET_PARA_CFG / CMD_SET_PARA_CFG).

Layout from WCH "CH9329芯片串口通信协议" V1.0 section 2.2.8. The doc states the byte order only for
the baud rate (big-endian). The 16-bit fields are assumed big-endian too, like pych9329-hid does.
UNVERIFIED: on a factory-default chip VID must read 0x1A86 and kb_release_delay_ms must read 1;
`ChipConfig.warnings()` flags a dump that looks byte-swapped.

New values are stored in flash and take effect at the next power-up, per the datasheet.
"""

from __future__ import annotations

from dataclasses import dataclass

SIZE = 50
COMMON_BAUDS = (9600, 19200, 38400, 57600, 115200)
FACTORY_VID = 0x1A86
FACTORY_PID = 0xE129

WORK_MODES = {
    0x00: "keyboard (normal + media) + mouse (abs + rel)",
    0x01: "keyboard only (normal keys, no media)",
    0x02: "mouse only (abs + rel)",
    0x03: "custom HID",
}
SERIAL_MODES = {0x00: "protocol", 0x01: "ASCII", 0x02: "transparent"}


@dataclass(frozen=True)
class Field:
    name: str
    offset: int
    size: int
    kind: str  # "int" (big-endian unsigned) or "raw"
    doc: str


FIELDS = [
    Field("work_mode", 0, 1, "int", "0x00-0x03 set by software, 0x80-0x83 selected by MODE pins"),
    Field("serial_mode", 1, 1, "int", "0x00-0x02 set by software, 0x80-0x82 selected by CFG pins"),
    Field("address", 2, 1, "int", "serial address; 0x00 accepts frames for any address"),
    Field("baud", 3, 4, "int", "serial baud rate"),
    Field("reserved_7", 7, 2, "raw", "reserved"),
    Field("packet_interval_ms", 9, 2, "int", "gap that ends a serial packet"),
    Field("vid", 11, 2, "int", "USB vendor id"),
    Field("pid", 13, 2, "int", "USB product id (differs per work mode)"),
    Field("kb_upload_interval_ms", 15, 2, "int", "ASCII mode only"),
    Field("kb_release_delay_ms", 17, 2, "int", "ASCII mode only"),
    Field("auto_enter", 19, 1, "int", "ASCII mode only"),
    Field("enter_chars", 20, 8, "raw", "ASCII mode only: 2 groups of 4 bytes"),
    Field("filter_strings", 28, 8, "raw", "ASCII mode only: 4-byte start + 4-byte end filter"),
    Field("usb_string_flags", 36, 1, "int", "bit7 custom strings, bit2 vendor, bit1 product, bit0 serial"),
    Field("kb_fast_upload", 37, 1, "int", "ASCII mode only"),
    Field("reserved_38", 38, 12, "raw", "reserved"),
]
FIELD_BY_NAME = {f.name: f for f in FIELDS}
assert sum(f.size for f in FIELDS) == SIZE


def _factory_bytes() -> bytes:
    data = bytearray(SIZE)
    for name, value in (
        ("work_mode", 0x80),
        ("serial_mode", 0x80),
        ("baud", 9600),
        ("packet_interval_ms", 3),
        ("vid", FACTORY_VID),
        ("pid", FACTORY_PID),
        ("kb_release_delay_ms", 1),
    ):
        f = FIELD_BY_NAME[name]
        data[f.offset : f.offset + f.size] = value.to_bytes(f.size, "big")
    data[20] = 0x0D  # enter on CR
    return bytes(data)


class ChipConfig:
    def __init__(self, data: bytes):
        if len(data) != SIZE:
            raise ValueError(f"config must be {SIZE} bytes, got {len(data)}")
        self._data = bytes(data)

    @classmethod
    def factory_default(cls) -> ChipConfig:
        """Defaults listed in the WCH doc; the reserved bytes of a real chip may differ."""
        return cls(_factory_bytes())

    def __eq__(self, other: object) -> bool:
        return isinstance(other, ChipConfig) and self._data == other._data

    def __repr__(self) -> str:
        return f"ChipConfig({self._data.hex()})"

    def to_bytes(self) -> bytes:
        return self._data

    def get(self, name: str) -> int | bytes:
        f = FIELD_BY_NAME[name]
        raw = self._data[f.offset : f.offset + f.size]
        return int.from_bytes(raw, "big") if f.kind == "int" else raw

    def replace(self, **changes: int | bytes) -> ChipConfig:
        data = bytearray(self._data)
        for name, value in changes.items():
            if name not in FIELD_BY_NAME:
                raise ValueError(f"unknown config field {name!r}; known: {', '.join(FIELD_BY_NAME)}")
            f = FIELD_BY_NAME[name]
            if f.kind == "int":
                if not isinstance(value, int) or not 0 <= value < 1 << (8 * f.size):
                    raise ValueError(f"{name} must be an integer that fits in {f.size} byte(s)")
                raw = value.to_bytes(f.size, "big")
            else:
                raw = bytes(value)
                if len(raw) != f.size:
                    raise ValueError(f"{name} must be exactly {f.size} bytes")
            data[f.offset : f.offset + f.size] = raw
        return ChipConfig(bytes(data))

    def diff(self, other: ChipConfig) -> list[tuple[str, int | bytes, int | bytes]]:
        return [(f.name, self.get(f.name), other.get(f.name)) for f in FIELDS if self.get(f.name) != other.get(f.name)]

    def for_write(self) -> tuple[ChipConfig, list[str]]:
        """Return the block to send with SET_PARA_CFG plus notes on anything adjusted.

        The doc only allows work mode 0x00-0x03 and serial mode 0x00-0x02 on write, while a factory
        chip reads back 0x80/0x80 (pin-selected). Writing the software equivalent keeps the same
        mode but stops following the MODE/CFG pins, which on a finished cable are fixed anyway.
        """
        notes = []
        cfg = self
        for name, limit in (("work_mode", 0x03), ("serial_mode", 0x02)):
            v = cfg.get(name)
            if v & 0x80 and (v & 0x7F) <= limit:
                cfg = cfg.replace(**{name: v & 0x7F})
                notes.append(f"{name} {v:#04x} (pin-selected) written as {v & 0x7F:#04x} (software, same mode)")
        return cfg, notes

    def validate_for_write(self) -> None:
        if self.get("work_mode") not in WORK_MODES:
            raise ValueError(f"work_mode must be one of {[hex(m) for m in WORK_MODES]}")
        if self.get("serial_mode") != 0x00:
            raise ValueError("serial_mode must stay 0x00 (protocol): other modes stop this driver from talking to the chip")
        if self.get("baud") not in COMMON_BAUDS:
            raise ValueError(f"baud must be one of {COMMON_BAUDS} (the rates the baud scanner can find)")
        if self.get("address") == 0xFF:
            raise ValueError("address 0xFF is the broadcast address: the chip would never answer again")

    def warnings(self) -> list[str]:
        out = []
        if self.get("vid") == int.from_bytes(FACTORY_VID.to_bytes(2, "little"), "big"):
            out.append("VID reads 0x861A: the 16-bit fields look little-endian, not big-endian. Report this.")
        if self.get("baud") not in COMMON_BAUDS:
            out.append(f"baud {self.get('baud')} is not a common rate: the byte order or layout may be wrong.")
        if self.get("work_mode") & 0x7F not in WORK_MODES or self.get("serial_mode") & 0x7F not in SERIAL_MODES:
            out.append("work_mode/serial_mode outside the documented values: the layout may be wrong.")
        return out

    def describe(self) -> list[str]:
        lines = []
        for f in FIELDS:
            v = self.get(f.name)
            if f.kind == "raw":
                if f.name.startswith("reserved"):
                    continue
                text = v.hex(" ")
            elif f.name == "work_mode":
                text = f"{v:#04x} {WORK_MODES.get(v & 0x7F, '?')} ({'MODE pins' if v & 0x80 else 'software'})"
            elif f.name == "serial_mode":
                text = f"{v:#04x} {SERIAL_MODES.get(v & 0x7F, '?')} ({'CFG pins' if v & 0x80 else 'software'})"
            elif f.name in ("address", "usb_string_flags"):
                text = f"{v:#04x}"
            elif f.name in ("vid", "pid"):
                text = f"{v:#06x}"
            else:
                text = str(v)
            lines.append(f"{f.name:22} {text}")
        return lines

    def to_dict(self) -> dict:
        fields = {}
        for f in FIELDS:
            v = self.get(f.name)
            fields[f.name] = v.hex(" ") if isinstance(v, bytes) else v
        return {"format": "ch9329-para-cfg", "hex": self._data.hex(" "), "fields": fields}

    @classmethod
    def from_dict(cls, d: dict) -> ChipConfig:
        if d.get("format") != "ch9329-para-cfg":
            raise ValueError("not a CH9329 config backup (missing format: ch9329-para-cfg)")
        return cls(bytes.fromhex(d["hex"]))


def parse_value(name: str, text: str) -> int | bytes:
    """Parse a CLI value for a field: integers accept 0x.. hex, raw fields take hex bytes."""
    if name not in FIELD_BY_NAME:
        raise ValueError(f"unknown config field {name!r}; known: {', '.join(FIELD_BY_NAME)}")
    if FIELD_BY_NAME[name].kind == "int":
        return int(text, 0)
    return bytes.fromhex(text)
