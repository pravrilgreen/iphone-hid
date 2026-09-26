package input

import (
	"context"
	"errors"
	"fmt"
	"math"
	"time"

	"github.com/pravrilgreen/iphone-hid/box/internal/hid"
)

// Actor sends the reports of one scripted action. Its methods stop at the first error; the error
// is returned by Engine.Do.
type Actor struct {
	e   *Engine
	ctx context.Context
	err error
}

// Err is the first error the action met.
func (a *Actor) Err() error { return a.err }

func (a *Actor) ok() bool {
	if a.err != nil {
		return false
	}
	if a.ctx != nil && a.ctx.Err() != nil {
		a.err = a.ctx.Err()
		return false
	}
	return true
}

func (a *Actor) pointer(p hid.Pointer) {
	if !a.ok() {
		return
	}
	if err := a.e.sink.Pointer(p); err != nil {
		a.err = err
		a.e.ptrKnown = false
		return
	}
	if p.X != a.e.ptr.X || p.Y != a.e.ptr.Y || !a.e.ptrKnown {
		dx := float64(int(p.X)-int(a.e.ptr.X)) / hid.AbsMax
		dy := float64(int(p.Y)-int(a.e.ptr.Y)) / hid.AbsMax
		if !a.e.ptrKnown || math.Hypot(dx, dy) > a.e.cfg.JumpDist {
			a.e.jumpAt = a.e.now()
		}
	}
	a.e.ptr = p
	a.e.ptr.Wheel = 0
	a.e.ptrKnown = true
}

// Sleep waits d (cut short if the action is cancelled).
func (a *Actor) Sleep(d time.Duration) {
	if !a.ok() || d <= 0 {
		return
	}
	if a.ctx == nil {
		time.Sleep(d)
		return
	}
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-t.C:
	case <-a.ctx.Done():
		a.err = a.ctx.Err()
	}
}

// Move puts the pointer at (x, y), normalized over the screen, keeping the buttons held.
func (a *Actor) Move(x, y float64) {
	p := a.e.ptr
	p.X, p.Y, p.Wheel = hid.AbsCoord(clamp01(x)), hid.AbsCoord(clamp01(y)), 0
	a.pointer(p)
}

// MoveSettled moves the pointer to (x, y) and waits until iOS has glided it there.
func (a *Actor) MoveSettled(x, y float64) {
	a.Move(x, y)
	if a.ok() {
		if d := a.e.jumpAt.Add(a.e.cfg.Settle).Sub(a.e.now()); d > 0 {
			a.Sleep(d)
		}
	}
}

// Buttons sets the pointer buttons held, where the pointer is.
func (a *Actor) Buttons(b uint8) {
	p := a.e.ptr
	p.Buttons, p.Wheel = b&7, 0
	a.pointer(p)
}

// Release lets every pointer button go, and repeats the release (it is idempotent) so that a
// report the phone missed cannot leave a finger down.
func (a *Actor) Release() {
	a.Buttons(0)
	for i := 0; i < a.e.cfg.ReleaseRepeat && a.ok(); i++ {
		a.Sleep(a.e.cfg.ReleaseGap)
		a.Buttons(0)
	}
}

// Tap touches (x, y) for hold (0: the default).
func (a *Actor) Tap(x, y float64, hold time.Duration) {
	if hold <= 0 {
		hold = a.e.cfg.TapHold
	}
	a.MoveSettled(x, y)
	a.Buttons(hid.ButtonPrimary)
	a.Sleep(hold)
	a.Release()
}

// Swipe touches (x1, y1), moves to (x2, y2) in duration and lifts while moving, so lists keep
// scrolling with the swipe's speed.
func (a *Actor) Swipe(x1, y1, x2, y2 float64, duration time.Duration) {
	a.MoveSettled(x1, y1)
	a.Buttons(hid.ButtonPrimary)
	a.path(x1, y1, x2, y2, duration, false)
	a.Release()
}

// Drag touches (x1, y1), holds it for hold (lifting an icon needs about half a second), moves to
// (x2, y2) in duration, stays still for rest so nothing keeps scrolling, then lifts.
func (a *Actor) Drag(x1, y1, x2, y2 float64, hold, duration, rest time.Duration) {
	a.MoveSettled(x1, y1)
	a.Buttons(hid.ButtonPrimary)
	a.Sleep(hold)
	a.path(x1, y1, x2, y2, duration, true)
	a.Sleep(rest)
	a.Release()
}

// path moves the pointer along a straight line, one report per Step. eased: slow in and out (a
// drag); otherwise constant speed (a swipe keeps its speed when it lifts).
func (a *Actor) path(x1, y1, x2, y2 float64, duration time.Duration, eased bool) {
	step := a.e.cfg.Step
	n := int(duration / step)
	if n < 1 {
		n = 1
	}
	start := a.e.now()
	for i := 1; i <= n && a.ok(); i++ {
		f := float64(i) / float64(n)
		if eased {
			f = f * f * (3 - 2*f)
		}
		a.Move(x1+(x2-x1)*f, y1+(y2-y1)*f)
		if d := start.Add(time.Duration(i) * step).Sub(a.e.now()); d > 0 {
			a.Sleep(d)
		}
	}
}

// Scroll turns the wheel by lines at (x, y): positive scrolls the content up (towards its top).
func (a *Actor) Scroll(x, y float64, lines int) {
	a.MoveSettled(x, y)
	dir := int8(1)
	if lines < 0 {
		dir, lines = -1, -lines
	}
	for i := 0; i < lines && a.ok(); i++ {
		p := a.e.ptr
		p.Wheel = dir
		a.pointer(p)
		a.Sleep(15 * time.Millisecond)
	}
}

// Key presses a key state for hold (0: the default), then releases it.
func (a *Actor) Key(ks hid.KeyState, hold time.Duration) {
	if hold <= 0 {
		hold = a.e.cfg.KeyHold
	}
	if !a.ok() {
		return
	}
	if err := a.e.sink.Keyboard(ks); err != nil {
		a.err = err
		return
	}
	a.e.keys = copyKeys(ks)
	a.Sleep(hold)
	a.keysUp()
}

func (a *Actor) keysUp() {
	if err := a.e.sink.Keyboard(hid.KeyState{}); err != nil {
		if a.err == nil {
			a.err = err
		}
		return
	}
	a.e.keys = hid.KeyState{}
}

// Type types text on a US keyboard layout. Nothing is sent if a character cannot be typed.
func (a *Actor) Type(text string) {
	presses, err := hid.TextPresses(text)
	if err != nil {
		a.err = err
		return
	}
	for _, ks := range presses {
		if !a.ok() {
			return
		}
		a.Key(ks, a.e.cfg.KeyHold)
		a.Sleep(a.e.cfg.KeyGap)
	}
}

// Combo presses a key combination such as "cmd+space".
func (a *Actor) Combo(combo string) {
	ks, err := hid.ParseCombo(combo)
	if err != nil {
		a.err = err
		return
	}
	a.Key(ks, 60*time.Millisecond)
}

// Consumer presses consumer controls (volume, mute...) for hold, then releases them.
func (a *Actor) Consumer(bits uint32, hold time.Duration) {
	if !a.ok() {
		return
	}
	if hold <= 0 {
		hold = 80 * time.Millisecond
	}
	if err := a.e.sink.Consumer(bits); err != nil {
		a.err = err
		return
	}
	a.e.consumer = bits
	a.Sleep(hold)
	if err := a.e.sink.Consumer(0); err != nil {
		if a.err == nil {
			a.err = err
		}
		return
	}
	a.e.consumer = 0
}

// PointerButton clicks a secondary or middle pointer button where the pointer is (AssistiveTouch
// runs the action set for that button).
func (a *Actor) PointerButton(b uint8) {
	a.Buttons(b)
	a.Sleep(a.e.cfg.TapHold)
	a.Release()
}

// Sync waits until the phone has taken every report sent.
func (a *Actor) Sync() {
	if !a.ok() {
		return
	}
	if err := a.e.sink.Sync(a.e.cfg.WriteTimeout); err != nil {
		a.err = err
	}
}

func (a *Actor) releaseEverything() {
	e := a.e
	if e.ptr.Buttons != 0 || !e.ptrKnown {
		p := e.ptr
		p.Buttons, p.Wheel = 0, 0
		if err := e.sink.Pointer(p); err == nil {
			e.ptr.Buttons = 0
		} else if a.err == nil {
			a.err = err
		}
	}
	if e.keys.Mods != 0 || len(e.keys.Keys) > 0 {
		a.keysUp()
	}
	if e.consumer != 0 {
		if err := e.sink.Consumer(0); err == nil {
			e.consumer = 0
		} else if a.err == nil {
			a.err = err
		}
	}
	if a.err == nil {
		// the action counts as done once the phone has taken its last report
		if err := e.sink.Sync(e.cfg.WriteTimeout); err != nil {
			a.err = err
		}
	}
}

// -- buttons -------------------------------------------------------------------------------------------

// Button is a phone button the box can press.
type Button struct {
	Name  string `json:"name"`
	Label string `json:"label"`
	How   string `json:"how"` // what the phone needs for it to work
	run   func(*Actor)
}

// Buttons are the phone buttons, in display order.
var Buttons = []Button{
	{Name: "home", Label: "Home", How: "keyboard shortcut Cmd+H",
		run: func(a *Actor) { a.Combo("cmd+h") }},
	{Name: "app_switcher", Label: "App Switcher",
		How: "middle pointer button; AssistiveTouch must map the middle button to App Switcher",
		run: func(a *Actor) { a.PointerButton(hid.ButtonMiddle) }},
	{Name: "spotlight", Label: "Search", How: "keyboard shortcut Cmd+Space",
		run: func(a *Actor) { a.Combo("cmd+space") }},
	{Name: "volume_up", Label: "Volume up", How: "consumer control Volume Increment",
		run: func(a *Actor) { a.Consumer(hid.ConsumerKeys["volume_up"], 0) }},
	{Name: "volume_down", Label: "Volume down", How: "consumer control Volume Decrement",
		run: func(a *Actor) { a.Consumer(hid.ConsumerKeys["volume_down"], 0) }},
	{Name: "mute", Label: "Mute", How: "consumer control Mute",
		run: func(a *Actor) { a.Consumer(hid.ConsumerKeys["mute"], 0) }},
	{Name: "play_pause", Label: "Play/Pause", How: "consumer control Play/Pause",
		run: func(a *Actor) { a.Consumer(hid.ConsumerKeys["play_pause"], 0) }},
}

// SetHomeMethod chooses how Home is pressed: "keys" (Cmd+H, the default) or "button" (the secondary
// pointer button, which AssistiveTouch must map to Home).
func SetHomeMethod(method string) error {
	for i, b := range Buttons {
		if b.Name != "home" {
			continue
		}
		switch method {
		case "keys", "":
			Buttons[i].How = "keyboard shortcut Cmd+H"
			Buttons[i].run = func(a *Actor) { a.Combo("cmd+h") }
		case "button":
			Buttons[i].How = "secondary pointer button; AssistiveTouch must map the secondary button to Home"
			Buttons[i].run = func(a *Actor) { a.PointerButton(hid.ButtonSecondary) }
		default:
			return fmt.Errorf("home method %q: keys or button", method)
		}
	}
	return nil
}

// PressButton presses the named phone button.
func (a *Actor) PressButton(name string) {
	for _, b := range Buttons {
		if b.Name == name {
			b.run(a)
			return
		}
	}
	a.err = fmt.Errorf("unknown button %q", name)
}

// ErrUnknownButton is returned for a button name that is not in Buttons.
var ErrUnknownButton = errors.New("unknown button")

// FindButton returns the named button.
func FindButton(name string) (Button, bool) {
	for _, b := range Buttons {
		if b.Name == name {
			return b, true
		}
	}
	return Button{}, false
}
