import pytest

from ihc.hid import protocol as p


def test_get_info_frame():
    assert p.get_info() == bytes.fromhex("57 ab 00 01 00 03")


def test_mouse_rel_frame():
    frame = p.mouse_rel(-1, 5, buttons=1)
    assert frame[:5] == bytes.fromhex("57 ab 00 05 05")
    assert frame[5:10] == bytes([0x01, 0x01, 0xFF, 0x05, 0x00])
    assert frame[-1] == sum(frame[:-1]) & 0xFF


def test_mouse_rel_range():
    with pytest.raises(p.FrameError):
        p.mouse_rel(128, 0)


def test_kb_general_pads_keys():
    frame = p.kb_general(0x08, [0x2C])  # Cmd+Space
    assert frame[5:13] == bytes([0x08, 0x00, 0x2C, 0, 0, 0, 0, 0])


def test_roundtrip():
    raw = p.encode(p.Cmd.GET_PARA_CFG, bytes(range(50)))
    f = p.decode(raw)
    assert f.cmd == p.Cmd.GET_PARA_CFG and f.data == bytes(range(50))


def test_decode_rejects_bad_checksum():
    raw = bytearray(p.get_info())
    raw[-1] ^= 0xFF
    with pytest.raises(p.FrameError):
        p.decode(bytes(raw))


def test_response_flags():
    ok = p.decode(p.encode(p.Cmd.SEND_MS_REL_DATA | 0x80, b"\x00"))
    err = p.decode(p.encode(p.Cmd.SEND_MS_REL_DATA | 0xC0, b"\xe4"))
    assert not ok.is_error and ok.request_cmd == p.Cmd.SEND_MS_REL_DATA
    assert err.is_error and err.request_cmd == p.Cmd.SEND_MS_REL_DATA


def test_parser_handles_garbage_and_split_chunks():
    a = p.encode(0x81, bytes(8))
    b = p.encode(0x85, b"\x00")
    stream = b"\x00\x57\x12" + a + b
    parser = p.FrameParser()
    out = []
    for i in range(0, len(stream), 3):
        out += parser.feed(stream[i : i + 3])
    assert [f.cmd for f in out] == [0x81, 0x85]
