"""US-layout keyboard usages (HID usage page 0x07) and helpers for text and key combos.

The iPhone must use the U.S. hardware keyboard layout (docs/iphone-setup.md) for text to come out right.
"""

from __future__ import annotations

MODIFIERS = {
    "ctrl": 0x01, "control": 0x01,
    "shift": 0x02,
    "alt": 0x04, "opt": 0x04, "option": 0x04,
    "cmd": 0x08, "command": 0x08, "gui": 0x08, "meta": 0x08, "win": 0x08, "super": 0x08,
    "rctrl": 0x10, "rshift": 0x20, "ralt": 0x40, "roption": 0x40, "rcmd": 0x80,
}
SHIFT = 0x02 | 0x20
# Modifiers that turn a key press into a shortcut rather than text.
SHORTCUT_MODS = 0x01 | 0x04 | 0x08 | 0x10 | 0x40 | 0x80

KEYS: dict[str, int] = {chr(ord("a") + i): 0x04 + i for i in range(26)}
KEYS.update({str(d): 0x1E + d - 1 for d in range(1, 10)})
KEYS.update({"0": 0x27})
KEYS.update(
    {
        "enter": 0x28, "return": 0x28,
        "esc": 0x29, "escape": 0x29,
        "backspace": 0x2A,
        "tab": 0x2B,
        "space": 0x2C,
        "minus": 0x2D, "equal": 0x2E, "lbracket": 0x2F, "rbracket": 0x30, "backslash": 0x31,
        "semicolon": 0x33, "quote": 0x34, "grave": 0x35, "comma": 0x36, "period": 0x37, "slash": 0x38,
        "capslock": 0x39,
        "printscreen": 0x46, "scrolllock": 0x47, "pause": 0x48,
        "insert": 0x49, "home": 0x4A, "pageup": 0x4B, "delete": 0x4C, "end": 0x4D, "pagedown": 0x4E,
        "right": 0x4F, "left": 0x50, "down": 0x51, "up": 0x52,
    }
)
KEYS.update({f"f{n}": 0x3A + n - 1 for n in range(1, 13)})

_UNSHIFTED = {"-": "minus", "=": "equal", "[": "lbracket", "]": "rbracket", "\\": "backslash",
              ";": "semicolon", "'": "quote", "`": "grave", ",": "comma", ".": "period", "/": "slash",
              " ": "space", "\n": "enter", "\t": "tab"}
_SHIFTED = {"!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6", "&": "7", "*": "8", "(": "9",
            ")": "0", "_": "minus", "+": "equal", "{": "lbracket", "}": "rbracket", "|": "backslash",
            ":": "semicolon", '"': "quote", "~": "grave", "<": "comma", ">": "period", "?": "slash"}

# char -> (modifiers, usage)
CHARS: dict[str, tuple[int, int]] = {}
for _c in "abcdefghijklmnopqrstuvwxyz0123456789":
    CHARS[_c] = (0, KEYS[_c])
    if _c.isalpha():
        CHARS[_c.upper()] = (0x02, KEYS[_c])
for _c, _name in _UNSHIFTED.items():
    CHARS[_c] = (0, KEYS[_name])
for _c, _name in _SHIFTED.items():
    CHARS[_c] = (0x02, KEYS[_name])

_BY_USAGE = {(mods, usage): c for c, (mods, usage) in CHARS.items()}
_KEY_NAMES = {}
for _name, _usage in KEYS.items():
    _KEY_NAMES.setdefault(_usage, _name)
_MOD_NAMES = [(0x01, "ctrl"), (0x02, "shift"), (0x04, "alt"), (0x08, "cmd"),
              (0x10, "rctrl"), (0x20, "rshift"), (0x40, "ralt"), (0x80, "rcmd")]


def text_reports(text: str) -> list[tuple[int, list[int]]]:
    """Keyboard reports that type `text`: a press then a release for every character."""
    bad = sorted({c for c in text if c not in CHARS})
    if bad:
        raise ValueError(
            f"cannot type {''.join(bad)!r} with a US layout; only printable ASCII, \\n and \\t are "
            "supported (for Vietnamese, type Telex/VNI keystrokes with the iOS Vietnamese keyboard)"
        )
    reports = []
    for c in text:
        mods, usage = CHARS[c]
        reports += [(mods, [usage]), (0, [])]
    return reports


def parse_combo(combo: str) -> tuple[int, list[int]]:
    """'cmd+space' -> (0x08, [0x2C]). Tokens are modifier names, key names or single characters."""
    mods, keys = 0, []
    for token in combo.lower().split("+"):
        token = token.strip()
        if token in MODIFIERS:
            mods |= MODIFIERS[token]
        elif token in KEYS:
            keys.append(KEYS[token])
        elif len(token) == 1 and token in CHARS:
            m, usage = CHARS[token]
            mods |= m
            keys.append(usage)
        else:
            raise ValueError(f"unknown key {token!r} in {combo!r}")
    if len(keys) > 6:
        raise ValueError("at most 6 non-modifier keys")
    return mods, keys


def char_for(mods: int, usage: int) -> str | None:
    """The character a US keyboard types for this press, or None for non-text keys and shortcuts."""
    if mods & SHORTCUT_MODS:
        return None
    return _BY_USAGE.get((0x02 if mods & SHIFT else 0, usage))


def combo_name(mods: int, usage: int) -> str:
    parts = [name for bit, name in _MOD_NAMES if mods & bit]
    return "+".join(parts + [_KEY_NAMES.get(usage, f"{usage:#04x}")])
