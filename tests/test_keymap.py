import pytest

from ihc.input import keymap


def test_parse_combo():
    assert keymap.parse_combo("cmd+space") == (0x08, [0x2C])
    assert keymap.parse_combo("CMD+Shift+3") == (0x0A, [0x20])
    assert keymap.parse_combo("esc") == (0, [0x29])
    assert keymap.parse_combo("cmd+?") == (0x0A, [0x38])  # '?' implies shift


def test_parse_combo_rejects_unknown_key():
    with pytest.raises(ValueError, match="unknown key"):
        keymap.parse_combo("cmd+nope")


def test_text_reports_press_then_release():
    assert keymap.text_reports("aA") == [(0, [0x04]), (0, []), (0x02, [0x04]), (0, [])]


def test_text_reports_rejects_non_ascii():
    with pytest.raises(ValueError, match="Telex"):
        keymap.text_reports("Tiếng Việt")


@pytest.mark.parametrize("char", sorted(keymap.CHARS))
def test_char_roundtrip(char):
    mods, usage = keymap.CHARS[char]
    assert keymap.char_for(mods, usage) == char


def test_shortcuts_are_not_text():
    assert keymap.char_for(0x08, keymap.KEYS["c"]) is None
    assert keymap.combo_name(0x08, keymap.KEYS["space"]) == "cmd+space"
    assert keymap.char_for(0x20, keymap.KEYS["a"]) == "A"  # right shift
