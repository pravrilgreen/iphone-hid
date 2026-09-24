from ihc.hid import protocol as p
from ihc.hid.config import ChipConfig
from ihc.hid.fake import FakeChip, SimPointer


def reply(chip, frame):
    out = chip.receive(frame)
    return p.FrameParser().feed(out)


def test_pointer_gain_grows_with_step_size():
    pt = SimPointer()
    x0 = pt.x
    pt.relative(4, 0, 0, 0)
    small = pt.x - x0
    x1 = pt.x
    pt.relative(40, 0, 0, 0)
    big = pt.x - x1
    assert big > 10 * small  # accelerated: 10x the units moves more than 10x as far


def test_pointer_is_clamped():
    pt = SimPointer(width=100, height=200)
    for _ in range(10):
        pt.relative(127, 127, 0, 0)
    assert (pt.x, pt.y) == (99, 199)


def test_split_and_merged_frames():
    chip = FakeChip()
    stream = p.get_info() + p.mouse_rel(1, 0)
    out = b"".join(chip.receive(stream[i : i + 1]) for i in range(len(stream)))
    frames = p.FrameParser().feed(out)
    assert [f.cmd for f in frames] == [0x81, 0x85]


def test_unknown_command_and_bad_length():
    chip = FakeChip()
    (f,) = reply(chip, p.encode(0x3E))
    assert f.is_error and f.status == p.Status.BAD_CMD
    (f,) = reply(chip, p.encode(p.Cmd.SEND_MS_REL_DATA, b"\x01\x00"))
    assert f.status == p.Status.BAD_PARAM


def test_address_rules():
    chip = FakeChip(ChipConfig.factory_default().replace(address=7))
    assert chip.receive(p.get_info(addr=0)) == b""
    assert reply(chip, p.get_info(addr=7))[0].cmd == 0x81
    assert chip.receive(p.mouse_rel(0, 0, 1, addr=0xFF)) == b""  # broadcast: executed, silent
    assert chip.pointer.buttons == 1
    open_chip = FakeChip()
    assert reply(open_chip, p.get_info(addr=0x42))[0].cmd == 0x81  # address 0 accepts any


def test_keyboard_text_backspace_and_capslock():
    chip = FakeChip()
    for mods, key in ((0, 0x04), (0, 0x05), (0, 0x2A), (0x02, 0x06), (0, 0x39)):
        chip.receive(p.kb_general(mods, [key]))
        chip.receive(p.kb_general(0, []))
    assert chip.keyboard.text == "aC"
    assert reply(chip, p.get_info())[0].data[2] == 0x02  # caps lock LED


def test_held_key_is_not_repeated():
    chip = FakeChip()
    chip.receive(p.kb_general(0, [0x04]))
    chip.receive(p.kb_general(0, [0x04, 0x05]))
    chip.receive(p.kb_general(0, []))
    assert chip.keyboard.text == "ab"


def test_set_default_restores_factory_block():
    chip = FakeChip(ChipConfig.factory_default().replace(baud=115200))
    chip.receive(p.encode(p.Cmd.SET_DEFAULT_CFG))
    chip.power_cycle()
    assert chip.baud == 9600
