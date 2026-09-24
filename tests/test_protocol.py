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


# Example frames printed in the WCH "CH9329芯片串口通信协议" V1.0 document.
WCH_EXAMPLES = [
    (lambda: p.kb_general(0, [0x04]), "57 AB 00 02 08 00 00 04 00 00 00 00 00 10"),
    (lambda: p.kb_general(0, []), "57 AB 00 02 08 00 00 00 00 00 00 00 00 0C"),
    (lambda: p.kb_general(0x02, [0x04]), "57 AB 00 02 08 02 00 04 00 00 00 00 00 12"),
    (lambda: p.kb_media(p.MEDIA_KEYS["mute"]), "57 AB 00 03 04 02 04 00 00 0F"),
    (lambda: p.kb_media(0), "57 AB 00 03 04 02 00 00 00 0B"),
    (lambda: p.mouse_abs(0, 0, buttons=p.MOUSE_LEFT), "57 AB 00 04 07 02 01 00 00 00 00 00 10"),
    (lambda: p.mouse_abs(0, 0), "57 AB 00 04 07 02 00 00 00 00 00 00 0F"),
    (lambda: p.mouse_abs(320, 533), "57 AB 00 04 07 02 00 40 01 15 02 00 67"),
    (lambda: p.mouse_abs(3097, 2667), "57 AB 00 04 07 02 00 19 0C 6B 0A 00 A9"),
    (lambda: p.mouse_rel(0, 0, buttons=p.MOUSE_LEFT), "57 AB 00 05 05 01 01 00 00 00 0E"),
    (lambda: p.mouse_rel(0, 0), "57 AB 00 05 05 01 00 00 00 00 0D"),
    (lambda: p.mouse_rel(-3, 0), "57 AB 00 05 05 01 00 FD 00 00 0A"),
    (lambda: p.mouse_rel(0, 5), "57 AB 00 05 05 01 00 00 05 00 12"),
]


@pytest.mark.parametrize("build,expected", WCH_EXAMPLES)
def test_wch_document_examples(build, expected):
    assert build() == bytes.fromhex(expected)


def test_acpi_and_media_bits():
    assert p.kb_acpi(p.ACPI_KEYS["sleep"])[5:7] == bytes([0x01, 0x02])
    frame = p.kb_media(p.MEDIA_KEYS["rewind"] | p.MEDIA_KEYS["volume_up"])
    assert frame[5:9] == bytes([0x02, 0x01, 0x00, 0x80])
    assert len(p.MEDIA_KEYS) == 24
    with pytest.raises(p.FrameError):
        p.kb_media(1 << 24)


def test_encode_limits_payload_to_64_bytes():
    p.encode(p.Cmd.SEND_MY_HID_DATA, bytes(64))
    with pytest.raises(p.FrameError):
        p.encode(p.Cmd.SEND_MY_HID_DATA, bytes(65))


def test_mouse_abs_range():
    with pytest.raises(p.FrameError):
        p.mouse_abs(4096, 0)


def test_parser_skips_impossible_length_and_counts_garbage():
    good = p.encode(0x81, bytes(8))
    parser = p.FrameParser()
    frames = parser.feed(b"\x57\xab\x00\x81\xff" + good)
    assert [f.cmd for f in frames] == [0x81]
    assert parser.discarded == 5
    assert parser.pending == 0


def test_status_of_one_byte_replies():
    assert p.decode(p.encode(0x85, b"\x00")).status == 0
    assert p.decode(p.encode(0x81, bytes(8))).status is None


def test_run_interval_units():
    frame = p.mouse_rel_run(1, 0, 3, 22.5, buttons=1, quarter=True)
    assert frame[5:10] == bytes([1, 0, 3, 90, 0x81])
    assert p.mouse_rel_run(1, 0, 3, 20)[8:10] == bytes([20, 0])
    import pytest

    with pytest.raises(p.FrameError):
        p.mouse_rel_run(1, 0, 3, 22.5)  # whole ms only without the flag
    with pytest.raises(p.FrameError):
        p.mouse_rel_run(1, 0, 3, 64.0, quarter=True)  # 256 quarter-ms units
