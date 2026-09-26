package hid

import (
	"errors"
	"fmt"
	"sort"
	"strings"
)

// Pointer buttons.
const (
	ButtonPrimary   = 1 // a touch
	ButtonSecondary = 2 // AssistiveTouch: the action set for the secondary button
	ButtonMiddle    = 4 // AssistiveTouch: the action set for the middle button
)

// Pointer is the whole state of the absolute pointer: a report carries all of it, so sending one
// again is harmless.
type Pointer struct {
	Buttons uint8
	X, Y    uint16 // 0..AbsMax over the whole screen
	Wheel   int8   // relative: lines to scroll, positive is up
}

// Report is the 6-byte absolute pointer report.
func (p Pointer) Report() []byte {
	return []byte{p.Buttons & 7, byte(p.X), byte(p.X >> 8), byte(p.Y), byte(p.Y >> 8), byte(p.Wheel)}
}

// AbsCoord maps a normalized coordinate (0 = left or top edge, 1 = right or bottom edge) to the
// pointer's range.
func AbsCoord(v float64) uint16 {
	if v != v || v <= 0 { // NaN too
		return 0
	}
	if v >= 1 {
		return AbsMax
	}
	return uint16(v*AbsMax + 0.5)
}

// KeyboardReport is the 8-byte boot keyboard report.
func KeyboardReport(mods uint8, keys []uint8) ([]byte, error) {
	if len(keys) > 6 {
		return nil, errors.New("at most 6 keys at once")
	}
	r := make([]byte, 8)
	r[0] = mods
	copy(r[2:], keys)
	return r, nil
}

// ConsumerReport is the 3-byte consumer control bitmap (bits of ConsumerUsages).
func ConsumerReport(bits uint32) []byte {
	return []byte{byte(bits), byte(bits >> 8), byte(bits >> 16)}
}

// ConsumerKeys names the consumer controls, as bits of the consumer report.
var ConsumerKeys = func() map[string]uint32 {
	names := []string{
		"volume_up", "volume_down", "mute", "play_pause",
		"next_track", "prev_track", "stop", "eject",
		"email", "search", "bookmarks", "ac_home",
		"back", "forward", "ac_stop", "refresh",
		"media", "explorer", "calculator", "screen_saver",
		"my_computer", "minimize", "record", "rewind",
	}
	m := make(map[string]uint32, len(names))
	for i, n := range names {
		m[n] = 1 << i
	}
	return m
}()

// ConsumerKeyNames lists ConsumerKeys in bit order.
func ConsumerKeyNames() []string {
	names := make([]string, 0, len(ConsumerKeys))
	for n := range ConsumerKeys {
		names = append(names, n)
	}
	sort.Slice(names, func(i, j int) bool { return ConsumerKeys[names[i]] < ConsumerKeys[names[j]] })
	return names
}

// Keyboard modifiers.
const (
	ModCtrl  = 0x01
	ModShift = 0x02
	ModAlt   = 0x04
	ModCmd   = 0x08
)

var modifiers = map[string]uint8{
	"ctrl": 0x01, "control": 0x01,
	"shift": 0x02,
	"alt":   0x04, "opt": 0x04, "option": 0x04,
	"cmd": 0x08, "command": 0x08, "gui": 0x08, "meta": 0x08, "win": 0x08, "super": 0x08,
	"rctrl": 0x10, "rshift": 0x20, "ralt": 0x40, "roption": 0x40, "rcmd": 0x80,
}

// KeyNamesHelp lists the names ParseCombo takes.
const KeyNamesHelp = "modifiers cmd, ctrl, alt (option), shift; keys a-z, 0-9, f1-f12, enter, esc, tab, space, " +
	"delete (backspace), forwarddelete, up, down, left, right, home, end, pageup, pagedown, capslock, " +
	"and printable characters such as - = [ ] ; ' , . /"

// Keys maps key names to keyboard usages (page 0x07).
var Keys = func() map[string]uint8 {
	k := map[string]uint8{
		// delete is the key a Mac and an iPhone keyboard call delete (backspace); forwarddelete (del)
		// deletes forwards
		"enter": 0x28, "return": 0x28, "esc": 0x29, "escape": 0x29, "backspace": 0x2A, "delete": 0x2A, "tab": 0x2B,
		"space": 0x2C, "minus": 0x2D, "equal": 0x2E, "lbracket": 0x2F, "rbracket": 0x30,
		"backslash": 0x31, "semicolon": 0x33, "quote": 0x34, "grave": 0x35, "comma": 0x36,
		"period": 0x37, "slash": 0x38, "capslock": 0x39, "printscreen": 0x46, "scrolllock": 0x47,
		"pause": 0x48, "insert": 0x49, "home": 0x4A, "pageup": 0x4B, "forwarddelete": 0x4C, "del": 0x4C, "end": 0x4D,
		"pagedown": 0x4E, "right": 0x4F, "left": 0x50, "down": 0x51, "up": 0x52, "0": 0x27,
	}
	for i := 0; i < 26; i++ {
		k[string(rune('a'+i))] = uint8(0x04 + i)
	}
	for d := 1; d <= 9; d++ {
		k[string(rune('0'+d))] = uint8(0x1E + d - 1)
	}
	for n := 1; n <= 12; n++ {
		k[fmt.Sprintf("f%d", n)] = uint8(0x3A + n - 1)
	}
	return k
}()

type keyPress struct {
	mods  uint8
	usage uint8
}

// chars maps each typeable character to its press on a US layout.
var chars = func() map[rune]keyPress {
	c := map[rune]keyPress{}
	for _, r := range "abcdefghijklmnopqrstuvwxyz0123456789" {
		c[r] = keyPress{0, Keys[string(r)]}
		if r >= 'a' && r <= 'z' {
			c[r-'a'+'A'] = keyPress{ModShift, Keys[string(r)]}
		}
	}
	unshifted := map[rune]string{'-': "minus", '=': "equal", '[': "lbracket", ']': "rbracket",
		'\\': "backslash", ';': "semicolon", '\'': "quote", '`': "grave", ',': "comma", '.': "period",
		'/': "slash", ' ': "space", '\n': "enter", '\t': "tab"}
	shifted := map[rune]string{'!': "1", '@': "2", '#': "3", '$': "4", '%': "5", '^': "6", '&': "7",
		'*': "8", '(': "9", ')': "0", '_': "minus", '+': "equal", '{': "lbracket", '}': "rbracket",
		'|': "backslash", ':': "semicolon", '"': "quote", '~': "grave", '<': "comma", '>': "period",
		'?': "slash"}
	for r, n := range unshifted {
		c[r] = keyPress{0, Keys[n]}
	}
	for r, n := range shifted {
		c[r] = keyPress{ModShift, Keys[n]}
	}
	return c
}()

// KeyState is one keyboard report's worth of state.
type KeyState struct {
	Mods uint8
	Keys []uint8
}

// TextPresses returns the key presses that type text on a US keyboard layout (each is followed
// by a release when sent). Every character is checked before anything is returned.
func TextPresses(text string) ([]KeyState, error) {
	var bad []string
	out := make([]KeyState, 0, len(text))
	for _, r := range text {
		p, ok := chars[r]
		if !ok {
			bad = append(bad, fmt.Sprintf("%q", r))
			continue
		}
		out = append(out, KeyState{p.mods, []uint8{p.usage}})
	}
	if bad != nil {
		return nil, fmt.Errorf("cannot type %s: only printable ASCII, newline and tab (US keyboard layout)",
			strings.Join(bad, ", "))
	}
	return out, nil
}

// ParseCombo parses a key combination such as "cmd+space", "cmd+shift+3" or "esc".
func ParseCombo(combo string) (KeyState, error) {
	var ks KeyState
	for _, tok := range strings.Split(strings.ToLower(combo), "+") {
		tok = strings.TrimSpace(tok)
		if m, ok := modifiers[tok]; ok {
			ks.Mods |= m
			continue
		}
		if u, ok := Keys[tok]; ok {
			ks.Keys = append(ks.Keys, u)
			continue
		}
		if r := []rune(tok); len(r) == 1 {
			if p, ok := chars[r[0]]; ok {
				ks.Mods |= p.mods
				ks.Keys = append(ks.Keys, p.usage)
				continue
			}
		}
		return KeyState{}, fmt.Errorf("unknown key %q in %q (%s)", tok, combo, KeyNamesHelp)
	}
	if len(ks.Keys) == 0 && ks.Mods == 0 {
		return KeyState{}, fmt.Errorf("no key in %q", combo)
	}
	if len(ks.Keys) > 6 {
		return KeyState{}, errors.New("at most 6 keys besides the modifiers")
	}
	return ks, nil
}
