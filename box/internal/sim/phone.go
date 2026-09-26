// Package sim is a simulated iPhone for development and tests: it takes the box's HID reports
// (absolute pointer, keyboard, consumer keys) and produces the HDMI frames of its screen, so the
// console, the API and the SDK run end to end without hardware.
//
// The phone has two home screen pages of apps, a scrolling Settings list, a Notes app that shows
// typed text, Search (Cmd+Space), an app switcher (middle button), Home (Cmd+H) and a volume HUD.
// Drags scroll with momentum, swipes flip home screen pages, taps open apps.
package sim

import (
	"context"
	"fmt"
	"math"
	"strings"
	"sync"
	"time"

	"github.com/pravrilgreen/iphone-hid/box/internal/hid"
	"github.com/pravrilgreen/iphone-hid/box/internal/video"
)

// Screen size in points (iPhone 15).
const (
	ScreenW = 393.0
	ScreenH = 852.0
)

// Frame is the HDMI mode the simulated phone mirrors into.
const (
	FrameW = 1920
	FrameH = 1080
)

type screenKind int

const (
	screenHome screenKind = iota
	screenApp
	screenSwitcher
	screenSearch
)

// App is one icon on the home screen.
type App struct {
	Name  string
	Color [3]uint8
}

// Apps on the two home screen pages.
var Apps = []App{
	{"Settings", [3]uint8{142, 142, 147}}, {"Notes", [3]uint8{255, 204, 0}},
	{"Photos", [3]uint8{255, 149, 0}}, {"Safari", [3]uint8{0, 122, 255}},
	{"Mail", [3]uint8{48, 176, 199}}, {"Maps", [3]uint8{52, 199, 89}},
	{"Clock", [3]uint8{28, 28, 30}}, {"Weather", [3]uint8{90, 200, 250}},
	{"Music", [3]uint8{255, 45, 85}}, {"Camera", [3]uint8{99, 99, 102}},
	{"Calendar", [3]uint8{255, 59, 48}}, {"Wallet", [3]uint8{0, 0, 0}},
	{"Health", [3]uint8{255, 55, 95}}, {"Files", [3]uint8{10, 132, 255}},
	{"Books", [3]uint8{255, 149, 0}}, {"Podcasts", [3]uint8{175, 82, 222}},
	{"Stocks", [3]uint8{28, 28, 30}}, {"Translate", [3]uint8{0, 122, 255}},
	{"Contacts", [3]uint8{142, 142, 147}}, {"Calculator", [3]uint8{255, 149, 0}},
	{"Reminders", [3]uint8{255, 255, 255}}, {"News", [3]uint8{255, 45, 85}},
	{"TV", [3]uint8{0, 0, 0}}, {"App Store", [3]uint8{0, 122, 255}},
}

const appsPerPage = 20

func pages() int { return (len(Apps) + appsPerPage - 1) / appsPerPage }

// Phone is a simulated iPhone. It is a hid.Sink and a video.Source.
type Phone struct {
	mu sync.Mutex

	// input as last received
	ptr      hid.Pointer
	ptrKnown bool
	keys     hid.KeyState
	consumer uint32
	reports  int

	// what the phone shows
	screen   screenKind
	app      int
	page     float64 // home screen page, fractional while swiping
	pageTo   float64 // the page it settles on
	scroll   float64 // list offset of the open app, points
	scrollV  float64 // points per second after a fling
	notes    string
	search   string
	volume   float64
	hudUntil time.Time
	recent   []int
	caps     bool

	// the touch in progress
	down      bool
	downAt    time.Time
	downPt    [2]float64
	lastPt    [2]float64
	lastMove  time.Time
	velocity  [2]float64
	dragging  bool
	dragAxis  int // 0 undecided, 1 horizontal, 2 vertical
	startPage float64
	startScr  float64

	fps    int
	render *renderer
	now    func() time.Time
}

// New returns a phone showing its home screen.
func New() *Phone {
	return &Phone{volume: 0.5, fps: 30, now: time.Now, render: newRenderer()}
}

// -- hid.Sink ------------------------------------------------------------------------------------------

// Pointer takes an absolute pointer report.
func (p *Phone) Pointer(r hid.Pointer) error {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.reports++
	x, y := float64(r.X)/hid.AbsMax*ScreenW, float64(r.Y)/hid.AbsMax*ScreenH
	now := p.now()
	was := p.ptr.Buttons
	if p.ptrKnown && now.After(p.lastMove) {
		dt := now.Sub(p.lastMove).Seconds()
		if dt > 0 && dt < 0.2 {
			vx, vy := (x-p.lastPt[0])/dt, (y-p.lastPt[1])/dt
			p.velocity[0] = 0.6*vx + 0.4*p.velocity[0]
			p.velocity[1] = 0.6*vy + 0.4*p.velocity[1]
		} else if dt >= 0.2 {
			p.velocity = [2]float64{}
		}
	}
	p.lastPt, p.lastMove = [2]float64{x, y}, now
	p.ptr, p.ptrKnown = r, true
	if r.Wheel != 0 && p.screen == screenApp {
		p.scroll -= float64(r.Wheel) * 40
		p.clampScroll()
	}
	pressed := r.Buttons &^ was
	released := was &^ r.Buttons
	switch {
	case pressed&hid.ButtonPrimary != 0:
		p.touchDown(x, y, now)
	case r.Buttons&hid.ButtonPrimary != 0 && p.down:
		p.touchMove(x, y)
	}
	if released&hid.ButtonPrimary != 0 && p.down {
		p.touchUp(x, y, now)
	}
	if pressed&hid.ButtonSecondary != 0 {
		p.goHome()
	}
	if pressed&hid.ButtonMiddle != 0 {
		p.screen = screenSwitcher
	}
	return nil
}

// Keyboard takes a keyboard report.
func (p *Phone) Keyboard(ks hid.KeyState) error {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.reports++
	prev := p.keys
	p.keys = hid.KeyState{Mods: ks.Mods, Keys: append([]uint8(nil), ks.Keys...)}
	for _, k := range ks.Keys {
		if contains(prev.Keys, k) {
			continue // held, not a new press
		}
		p.keyPress(ks.Mods, k)
	}
	return nil
}

// Consumer takes a consumer control report.
func (p *Phone) Consumer(bits uint32) error {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.reports++
	pressed := bits &^ p.consumer
	p.consumer = bits
	switch {
	case pressed&hid.ConsumerKeys["volume_up"] != 0:
		p.volume = math.Min(1, p.volume+1.0/16)
		p.hudUntil = p.now().Add(1500 * time.Millisecond)
	case pressed&hid.ConsumerKeys["volume_down"] != 0:
		p.volume = math.Max(0, p.volume-1.0/16)
		p.hudUntil = p.now().Add(1500 * time.Millisecond)
	case pressed&hid.ConsumerKeys["mute"] != 0:
		p.volume = 0
		p.hudUntil = p.now().Add(1500 * time.Millisecond)
	}
	return nil
}

// Sync returns at once: the simulated phone takes every report.
func (p *Phone) Sync(time.Duration) error { return nil }

// Link reports a phone that enumerated the gadget.
func (p *Phone) Link() hid.Link {
	return hid.Link{UDC: "sim", State: "configured", Connected: true, Profile: hid.DefaultProfile}
}

// Wake does nothing: the simulated phone never sleeps.
func (p *Phone) Wake(time.Duration) (string, string, error) { return "configured", "configured", nil }

// Close does nothing.
func (p *Phone) Close() error { return nil }

var _ hid.Sink = (*Phone)(nil)

// -- behaviour ------------------------------------------------------------------------------------------

func (p *Phone) keyPress(mods, key uint8) {
	cmd := mods&(hid.ModCmd|0x80) != 0
	switch {
	case cmd && key == hid.Keys["h"]:
		p.goHome()
		return
	case cmd && key == hid.Keys["space"]:
		p.screen, p.search = screenSearch, ""
		return
	case key == hid.Keys["esc"] && p.screen == screenSearch:
		p.goHome()
		return
	case key == hid.Keys["capslock"]:
		p.caps = !p.caps
		return
	case cmd:
		return
	}
	ch := charFor(mods, key, p.caps)
	switch {
	case p.screen == screenSearch:
		if key == hid.Keys["backspace"] {
			p.search = dropLast(p.search)
		} else if key == hid.Keys["enter"] {
			for i, a := range Apps {
				if p.search != "" && strings.HasPrefix(strings.ToLower(a.Name), strings.ToLower(p.search)) {
					p.open(i)
					return
				}
			}
		} else if ch != "" {
			p.search += ch
		}
	case p.screen == screenApp && Apps[p.app].Name == "Notes":
		if key == hid.Keys["backspace"] {
			p.notes = dropLast(p.notes)
		} else if ch != "" {
			p.notes += ch
		}
	}
}

func (p *Phone) goHome() {
	if p.screen == screenApp {
		p.remember(p.app)
	}
	p.screen, p.scroll, p.scrollV = screenHome, 0, 0
}

func (p *Phone) open(i int) {
	p.screen, p.app, p.scroll, p.scrollV = screenApp, i, 0, 0
	p.remember(i)
}

func (p *Phone) remember(i int) {
	out := []int{i}
	for _, r := range p.recent {
		if r != i && len(out) < 4 {
			out = append(out, r)
		}
	}
	p.recent = out
}

func (p *Phone) touchDown(x, y float64, now time.Time) {
	p.down, p.downAt, p.downPt = true, now, [2]float64{x, y}
	p.dragging, p.dragAxis = false, 0
	p.velocity = [2]float64{}
	p.startPage, p.startScr = p.page, p.scroll
	p.scrollV = 0
}

func (p *Phone) touchMove(x, y float64) {
	dx, dy := x-p.downPt[0], y-p.downPt[1]
	if !p.dragging && math.Hypot(dx, dy) > 8 {
		p.dragging = true
		if math.Abs(dx) > math.Abs(dy) {
			p.dragAxis = 1
		} else {
			p.dragAxis = 2
		}
	}
	if !p.dragging {
		return
	}
	switch {
	case p.screen == screenHome && p.dragAxis == 1:
		p.page = math.Max(-0.3, math.Min(1.3, p.startPage-dx/ScreenW))
	case p.screen == screenApp && p.dragAxis == 2:
		p.scroll = p.startScr - dy
		p.clampScroll()
	}
}

func (p *Phone) touchUp(x, y float64, now time.Time) {
	p.down = false
	if !p.dragging {
		p.tap(x, y)
		return
	}
	switch {
	case p.screen == screenHome && p.dragAxis == 1:
		target := math.Round(p.page)
		if v := p.velocity[0]; v < -300 {
			target = math.Ceil(p.startPage + 0.01)
		} else if v > 300 {
			target = math.Floor(p.startPage - 0.01)
		}
		p.pageTo = math.Max(0, math.Min(float64(pages()-1), target))
	case p.screen == screenApp && p.dragAxis == 2:
		p.scrollV = -p.velocity[1]
	case p.screen == screenHome && p.dragAxis == 2 && y-p.downPt[1] > 80:
		p.screen, p.search = screenSearch, "" // pull down on the home screen: Search
	}
}

func (p *Phone) tap(x, y float64) {
	switch p.screen {
	case screenHome:
		if i := p.appAt(x, y); i >= 0 {
			p.open(i)
		}
	case screenSwitcher:
		for n, i := range p.recent {
			cx := 40 + float64(n%2)*170
			cy := 160 + float64(n/2)*300
			if x >= cx && x <= cx+150 && y >= cy && y <= cy+270 {
				p.open(i)
				return
			}
		}
		p.goHome()
	case screenSearch:
		if y > 140 {
			p.goHome()
		}
	case screenApp:
		if y < 100 && x < 120 {
			p.goHome() // "< Back"
		}
	}
}

// appAt returns the app under a point of the home screen, or -1.
func (p *Phone) appAt(x, y float64) int {
	page := int(math.Round(p.page))
	for i := 0; i < appsPerPage; i++ {
		idx := page*appsPerPage + i
		if idx >= len(Apps) {
			break
		}
		ix, iy := iconPos(i)
		if x >= ix && x <= ix+iconSize && y >= iy && y <= iy+iconSize {
			return idx
		}
	}
	return -1
}

const iconSize = 62.0

func iconPos(i int) (float64, float64) {
	col, row := i%4, i/4
	return 27 + float64(col)*(iconSize+27), 90 + float64(row)*(iconSize+42)
}

func (p *Phone) listLen() float64 { return 40 * 64 }

func (p *Phone) clampScroll() {
	max := p.listLen() - (ScreenH - 140)
	if p.scroll < 0 {
		p.scroll = 0
		p.scrollV = 0
	}
	if p.scroll > max {
		p.scroll = max
		p.scrollV = 0
	}
}

// step advances the animations by dt.
func (p *Phone) step(dt float64) {
	if p.scrollV != 0 && !p.down {
		p.scroll += p.scrollV * dt
		p.scrollV *= math.Pow(0.08, dt) // iOS-like deceleration
		if math.Abs(p.scrollV) < 5 {
			p.scrollV = 0
		}
		p.clampScroll()
	}
	if !p.down && p.screen == screenHome && p.page != p.pageTo {
		p.page += (p.pageTo - p.page) * math.Min(1, dt*12)
		if math.Abs(p.pageTo-p.page) < 0.002 {
			p.page = p.pageTo
		}
	}
}

// -- state for tests and the API -------------------------------------------------------------------------

// State is what the simulated phone shows, for tests.
type State struct {
	Screen  string     `json:"screen"` // home, app, switcher, search
	App     string     `json:"app,omitempty"`
	Page    int        `json:"page"`
	Scroll  float64    `json:"scroll"`
	Notes   string     `json:"notes"`
	Search  string     `json:"search"`
	Volume  float64    `json:"volume"`
	Pointer [2]float64 `json:"pointer"`
	Pressed bool       `json:"pressed"`
	Reports int        `json:"reports"`
}

// State returns what the phone shows now.
func (p *Phone) State() State {
	p.mu.Lock()
	defer p.mu.Unlock()
	s := State{Screen: [...]string{"home", "app", "switcher", "search"}[p.screen], Page: int(math.Round(p.page)),
		Scroll: p.scroll, Notes: p.notes, Search: p.search, Volume: p.volume, Pressed: p.ptr.Buttons&1 != 0,
		Pointer: [2]float64{float64(p.ptr.X) / hid.AbsMax, float64(p.ptr.Y) / hid.AbsMax}, Reports: p.reports}
	if p.screen == screenApp {
		s.App = Apps[p.app].Name
	}
	return s
}

// -- video.Source ---------------------------------------------------------------------------------------

// Describe names the source.
func (p *Phone) Describe() string { return "simulated iPhone" }

// Run renders the screen into 1080p NV12 frames until ctx ends.
func (p *Phone) Run(ctx context.Context, emit func(*video.Raw)) error {
	tick := time.NewTicker(time.Second / time.Duration(p.fps))
	defer tick.Stop()
	last := p.now()
	for {
		select {
		case <-ctx.Done():
			return nil
		case <-tick.C:
		}
		now := p.now()
		p.mu.Lock()
		p.step(now.Sub(last).Seconds())
		last = now
		raw := p.render.frame(p, now)
		p.mu.Unlock()
		emit(raw)
	}
}

var _ video.Source = (*Phone)(nil)

// -- helpers ------------------------------------------------------------------------------------------------

func contains(list []uint8, v uint8) bool {
	for _, x := range list {
		if x == v {
			return true
		}
	}
	return false
}

func dropLast(s string) string {
	r := []rune(s)
	if len(r) == 0 {
		return s
	}
	return string(r[:len(r)-1])
}

var usageChars = func() map[uint8][2]string {
	m := map[uint8][2]string{}
	for i := 0; i < 26; i++ {
		c := string(rune('a' + i))
		m[uint8(0x04+i)] = [2]string{c, strings.ToUpper(c)}
	}
	digits, shifted := "1234567890", "!@#$%^&*()"
	for i := 0; i < 10; i++ {
		m[uint8(0x1E+i)] = [2]string{digits[i : i+1], shifted[i : i+1]}
	}
	for k, v := range map[uint8][2]string{0x2C: {" ", " "}, 0x2D: {"-", "_"}, 0x2E: {"=", "+"}, 0x2F: {"[", "{"},
		0x30: {"]", "}"}, 0x31: {"\\", "|"}, 0x33: {";", ":"}, 0x34: {"'", "\""}, 0x35: {"`", "~"},
		0x36: {",", "<"}, 0x37: {".", ">"}, 0x38: {"/", "?"}, 0x28: {"\n", "\n"}} {
		m[k] = v
	}
	return m
}()

func charFor(mods, key uint8, caps bool) string {
	c, ok := usageChars[key]
	if !ok {
		return ""
	}
	shift := mods&(hid.ModShift|0x20) != 0
	if caps && key >= 0x04 && key <= 0x1D {
		shift = !shift
	}
	if shift {
		return c[1]
	}
	return c[0]
}

// Clock shows the phone's time in the status bar.
func Clock(t time.Time) string { return fmt.Sprintf("%d:%02d", t.Hour(), t.Minute()) }
