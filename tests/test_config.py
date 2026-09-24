import pytest

from ihc.hid.config import FIELDS, ChipConfig, parse_value


def test_factory_defaults_match_the_wch_document():
    cfg = ChipConfig.factory_default()
    assert cfg.get("work_mode") == 0x80
    assert cfg.get("serial_mode") == 0x80
    assert cfg.get("baud") == 9600
    assert cfg.to_bytes()[3:7] == bytes.fromhex("00 00 25 80")  # big-endian, as documented
    assert cfg.get("packet_interval_ms") == 3
    assert (cfg.get("vid"), cfg.get("pid")) == (0x1A86, 0xE129)
    assert cfg.get("kb_release_delay_ms") == 1
    assert cfg.warnings() == []


def test_layout_covers_50_bytes_without_gaps():
    offset = 0
    for f in FIELDS:
        assert f.offset == offset
        offset += f.size
    assert offset == 50


def test_replace_and_diff():
    a = ChipConfig.factory_default()
    b = a.replace(baud=115200, address=3)
    assert b.get("baud") == 115200 and a.get("baud") == 9600
    assert {name for name, _, _ in a.diff(b)} == {"baud", "address"}
    with pytest.raises(ValueError):
        a.replace(address=256)
    with pytest.raises(ValueError):
        a.replace(nope=1)
    with pytest.raises(ValueError):
        a.replace(enter_chars=b"\x00")


def test_for_write_turns_pin_modes_into_software_modes():
    cfg, notes = ChipConfig.factory_default().for_write()
    assert cfg.get("work_mode") == 0x00 and cfg.get("serial_mode") == 0x00
    assert len(notes) == 2
    cfg.validate_for_write()


@pytest.mark.parametrize(
    "changes,match",
    [({"serial_mode": 1}, "serial_mode"), ({"baud": 12345}, "baud"), ({"work_mode": 4}, "work_mode")],
)
def test_validate_for_write_rejects_dangerous_values(changes, match):
    cfg, _ = ChipConfig.factory_default().replace(**changes).for_write()
    with pytest.raises(ValueError, match=match):
        cfg.validate_for_write()


def test_byte_swapped_dump_is_flagged():
    cfg = ChipConfig.factory_default().replace(vid=0x861A)
    assert any("little-endian" in w for w in cfg.warnings())


def test_dict_roundtrip_and_parse_value():
    cfg = ChipConfig.factory_default().replace(work_mode=1)
    assert ChipConfig.from_dict(cfg.to_dict()) == cfg
    with pytest.raises(ValueError):
        ChipConfig.from_dict({"hex": cfg.to_bytes().hex()})
    assert parse_value("baud", "0x1C200") == 115200
    assert parse_value("enter_chars", "0d 00 00 00 00 00 00 00") == bytes([13, 0, 0, 0, 0, 0, 0, 0])
    assert "work_mode" in "\n".join(cfg.describe())
