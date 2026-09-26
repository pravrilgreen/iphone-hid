// Package input drives the phone's touch and keyboard through a hid.Sink: live input streamed from
// a browser, and scripted gestures (tap, swipe, drag, type...) for test frameworks.
//
// One goroutine sends every report, so presses, releases and key changes reach the phone in the
// order they were made. Motion keeps only the newest position: while a report waits for the phone
// to take the previous one, newer moves replace the pending one, so the pointer is never behind
// the operator's hand. A press never merges with anything: it lands where it was made.
//
// iOS glides the pointer to a new absolute position instead of jumping. A press that follows a
// jump therefore waits (Config.Settle) until the pointer got there; a mouse hovering over the
// live view keeps the pointer in place already, so its clicks go out at once.
package input

import (
	"context"
	"errors"
	"fmt"
	"math"
	"sort"
	"sync"
	"time"

	"github.com/pravrilgreen/iphone-hid/box/internal/hid"
)

// Config holds the timings of the touch engine.
type Config struct {
	Settle        time.Duration // wait after a jump before pressing: iOS glides the pointer there
	JumpDist      float64       // a move longer than this (fraction of the screen diagonal) is a jump
	TapHold       time.Duration // how long a tap holds the press
	LongPress     time.Duration // default long press
	ReleaseRepeat int           // extra releases after a scripted gesture (a release report is idempotent)
	ReleaseGap    time.Duration
	KeyHold       time.Duration // a scripted key press
	KeyGap        time.Duration // between typed characters
	Step          time.Duration // report interval of scripted swipes and drags
	Stale         time.Duration // live presses and moves older than this are dropped, releases never
	WriteTimeout  time.Duration // how long a report may wait for the phone to take the previous one
}

// DefaultConfig holds timings measured by projects that drive iPhones this way (a tap needs the
// pointer settled and a press of at least about 60 ms; a long press about 1.5 s).
var DefaultConfig = Config{
	Settle:        80 * time.Millisecond,
	JumpDist:      0.01,
	TapHold:       80 * time.Millisecond,
	LongPress:     1500 * time.Millisecond,
	ReleaseRepeat: 2,
	ReleaseGap:    15 * time.Millisecond,
	KeyHold:       30 * time.Millisecond,
	KeyGap:        20 * time.Millisecond,
	Step:          8 * time.Millisecond,
	Stale:         500 * time.Millisecond,
	WriteTimeout:  250 * time.Millisecond,
}

// Touch is a live pointer state from an operator: position normalized over the phone screen
// (0,0 top left, 1,1 bottom right), buttons held, and wheel lines to scroll.
type Touch struct {
	X, Y    float64
	Buttons uint8
	Wheel   int
}

type opKind int

const (
	opTouch opKind = iota
	opKeys
	opConsumer
	opRelease
	opAction
)

type op struct {
	kind     opKind
	touch    Touch
	keys     hid.KeyState
	consumer uint32
	at       time.Time
	name     string
	run      func(*Actor) error
	done     chan error
	ctx      context.Context
}

// Engine sends reports to the phone. Create it with New and stop it with Close.
type Engine struct {
	sink hid.Sink
	cfg  Config
	now  func() time.Time

	mu     sync.Mutex
	queue  []*op
	signal chan struct{}
	closed bool
	busy   string

	// as last sent to the phone (engine goroutine only)
	ptr      hid.Pointer
	ptrKnown bool
	jumpAt   time.Time
	keys     hid.KeyState
	consumer uint32

	stats    statsWindow
	lastErr  error
	errAt    time.Time
	errCount int
	listen   []chan Event
	done     chan struct{}
}

// Event tells listeners (the live control sockets) what happened to the input.
type Event struct {
	Kind  string // "error", "dropped"
	Error string
	Count int
}

// New starts an engine on sink.
func New(sink hid.Sink, cfg Config) *Engine {
	e := &Engine{sink: sink, cfg: cfg, now: time.Now, signal: make(chan struct{}, 1), done: make(chan struct{})}
	go e.loop()
	return e
}

// Close stops the engine after releasing everything.
func (e *Engine) Close() {
	e.mu.Lock()
	if e.closed {
		e.mu.Unlock()
		return
	}
	e.closed = true
	e.mu.Unlock()
	e.wakeup()
	<-e.done
}

// Sink returns the sink the engine writes to.
func (e *Engine) Sink() hid.Sink { return e.sink }

// Config returns the engine's timings.
func (e *Engine) Config() Config { return e.cfg }

// Busy names the scripted action running, if any.
func (e *Engine) Busy() string {
	e.mu.Lock()
	defer e.mu.Unlock()
	return e.busy
}

// Live queues an operator's pointer state. Consecutive states with the same buttons merge (the
// newest position wins, wheel lines add up); a change of buttons never merges.
func (e *Engine) Live(t Touch) {
	t.X, t.Y = clamp01(t.X), clamp01(t.Y)
	t.Buttons &= 7
	now := e.now()
	e.mu.Lock()
	if n := len(e.queue); n > 0 {
		tail := e.queue[n-1]
		if tail.kind == opTouch && tail.touch.Buttons == t.Buttons && abs(tail.touch.Wheel+t.Wheel) <= 127 {
			tail.touch.X, tail.touch.Y = t.X, t.Y
			tail.touch.Wheel += t.Wheel
			e.stats.merged++
			e.mu.Unlock()
			return
		}
	}
	e.queue = append(e.queue, &op{kind: opTouch, touch: t, at: now})
	e.mu.Unlock()
	e.wakeup()
}

// LiveKeys queues the operator's keyboard state (modifiers and up to six keys held).
func (e *Engine) LiveKeys(ks hid.KeyState) { e.push(&op{kind: opKeys, keys: ks, at: e.now()}) }

// LiveConsumer queues the consumer controls held (volume, mute...).
func (e *Engine) LiveConsumer(bits uint32) {
	e.push(&op{kind: opConsumer, consumer: bits, at: e.now()})
}

// ReleaseAll queues a release of every button and key.
func (e *Engine) ReleaseAll() { e.push(&op{kind: opRelease, at: e.now()}) }

// Do runs a scripted action in order with live input and waits for it. The action gets an Actor
// that sends reports and sleeps; everything is released when it ends, even after an error.
func (e *Engine) Do(ctx context.Context, name string, run func(*Actor) error) error {
	o := &op{kind: opAction, name: name, run: run, done: make(chan error, 1), at: e.now(), ctx: ctx}
	if !e.push(o) {
		return errors.New("the input engine is stopped")
	}
	select {
	case err := <-o.done:
		return err
	case <-ctx.Done():
		return ctx.Err()
	}
}

// Subscribe returns a channel of events; call the returned function to stop.
func (e *Engine) Subscribe() (<-chan Event, func()) {
	ch := make(chan Event, 16)
	e.mu.Lock()
	e.listen = append(e.listen, ch)
	e.mu.Unlock()
	return ch, func() {
		e.mu.Lock()
		defer e.mu.Unlock()
		for i, c := range e.listen {
			if c == ch {
				e.listen = append(e.listen[:i], e.listen[i+1:]...)
				close(ch)
				return
			}
		}
	}
}

func (e *Engine) push(o *op) bool {
	e.mu.Lock()
	if e.closed {
		e.mu.Unlock()
		return false
	}
	e.queue = append(e.queue, o)
	e.mu.Unlock()
	e.wakeup()
	return true
}

func (e *Engine) wakeup() {
	select {
	case e.signal <- struct{}{}:
	default:
	}
}

func (e *Engine) next() (*op, bool) {
	for {
		e.mu.Lock()
		if len(e.queue) > 0 {
			o := e.queue[0]
			e.queue[0] = nil
			e.queue = e.queue[1:]
			e.mu.Unlock()
			return o, true
		}
		closed := e.closed
		e.mu.Unlock()
		if closed {
			return nil, false
		}
		<-e.signal
	}
}

func (e *Engine) loop() {
	defer close(e.done)
	for {
		o, ok := e.next()
		if !ok {
			e.releaseAll()
			e.mu.Lock()
			for _, q := range e.queue {
				if q.done != nil {
					q.done <- errors.New("the input engine stopped")
				}
			}
			e.queue = nil
			e.mu.Unlock()
			return
		}
		switch o.kind {
		case opTouch:
			e.liveTouch(o)
		case opKeys:
			e.liveKeys(o)
		case opConsumer:
			e.liveConsumer(o)
		case opRelease:
			e.releaseAll()
		case opAction:
			e.action(o)
		}
	}
}

// -- live input ----------------------------------------------------------------------------------

func (e *Engine) stale(o *op) bool { return e.now().Sub(o.at) > e.cfg.Stale }

func (e *Engine) liveTouch(o *op) {
	t := o.touch
	pressed := t.Buttons &^ e.ptr.Buttons
	target := hid.Pointer{Buttons: t.Buttons, X: hid.AbsCoord(t.X), Y: hid.AbsCoord(t.Y), Wheel: int8(clampInt(t.Wheel, -127, 127))}
	if e.stale(o) {
		// too late to be what the operator meant: keep only the releases
		keep := t.Buttons & e.ptr.Buttons
		e.dropped(1)
		if keep != e.ptr.Buttons {
			rel := e.ptr
			rel.Buttons, rel.Wheel = keep, 0
			e.send(rel, o.at)
		}
		return
	}
	since := o.at
	if pressed != 0 && e.isJump(target) {
		// move there with the buttons as they are, let the pointer glide, then press
		move := target
		move.Buttons, move.Wheel = e.ptr.Buttons, 0
		if e.send(move, o.at) {
			e.settle()
			since = e.now() // the stats measure the box, not this deliberate wait
		}
	}
	e.send(target, since)
}

func (e *Engine) liveKeys(o *op) {
	if e.stale(o) && !releasesOnly(e.keys, o.keys) {
		e.dropped(1)
		o.keys = hid.KeyState{Mods: o.keys.Mods & e.keys.Mods, Keys: intersect(o.keys.Keys, e.keys.Keys)}
	}
	if err := e.sink.Keyboard(o.keys); err != nil {
		e.fail(err)
		return
	}
	e.keys = copyKeys(o.keys)
	e.record(e.now().Sub(o.at))
}

func (e *Engine) liveConsumer(o *op) {
	bits := o.consumer
	if e.stale(o) {
		bits &= e.consumer
	}
	if err := e.sink.Consumer(bits); err != nil {
		e.fail(err)
		return
	}
	e.consumer = bits
	e.record(e.now().Sub(o.at))
}

// isJump tells whether moving the pointer to p is far enough that iOS needs time to glide there.
func (e *Engine) isJump(p hid.Pointer) bool {
	if !e.ptrKnown {
		return true
	}
	dx := float64(int(p.X)-int(e.ptr.X)) / hid.AbsMax
	dy := float64(int(p.Y)-int(e.ptr.Y)) / hid.AbsMax
	return math.Hypot(dx, dy) > e.cfg.JumpDist || e.now().Sub(e.jumpAt) < e.cfg.Settle
}

// settle waits until the pointer finished gliding to its last position.
func (e *Engine) settle() {
	if d := e.jumpAt.Add(e.cfg.Settle).Sub(e.now()); d > 0 {
		time.Sleep(d)
	}
}

// send writes one pointer report and records it. since: when the input was made (for the stats).
func (e *Engine) send(p hid.Pointer, since time.Time) bool {
	jump := e.isJump(p)
	if err := e.sink.Pointer(p); err != nil {
		e.fail(err)
		e.ptrKnown = false
		return false
	}
	if jump && (p.X != e.ptr.X || p.Y != e.ptr.Y || !e.ptrKnown) {
		e.jumpAt = e.now()
	}
	e.ptr = p
	e.ptr.Wheel = 0
	e.ptrKnown = true
	if !since.IsZero() {
		e.record(e.now().Sub(since))
	}
	return true
}

// record adds one report's latency to the stats.
func (e *Engine) record(d time.Duration) {
	e.mu.Lock()
	e.stats.add(d)
	e.mu.Unlock()
}

// here is where a report that does not move the pointer puts it: where it is, or the centre of the
// screen when the box does not know (after a start or an error), rather than the corner at 0, 0.
func (e *Engine) here() hid.Pointer {
	p := e.ptr
	if !e.ptrKnown {
		p.X, p.Y = hid.AbsCoord(0.5), hid.AbsCoord(0.5)
	}
	return p
}

// placed records a pointer report the phone took.
func (e *Engine) placed(p hid.Pointer) {
	if !e.ptrKnown || p.X != e.ptr.X || p.Y != e.ptr.Y {
		e.jumpAt = e.now()
	}
	e.ptr, e.ptr.Wheel, e.ptrKnown = p, 0, true
}

func (e *Engine) releaseAll() {
	var first error
	if e.ptr.Buttons != 0 || !e.ptrKnown {
		p := e.here()
		p.Buttons, p.Wheel = 0, 0
		if err := e.sink.Pointer(p); err != nil {
			first = err
		} else {
			e.placed(p)
		}
	}
	if err := e.sink.Keyboard(hid.KeyState{}); err != nil && first == nil {
		first = err
	} else if err == nil {
		e.keys = hid.KeyState{}
	}
	if err := e.sink.Consumer(0); err != nil && first == nil {
		first = err
	} else if err == nil {
		e.consumer = 0
	}
	if first != nil {
		e.fail(first)
	}
}

// -- scripted actions ------------------------------------------------------------------------------

func (e *Engine) action(o *op) {
	if o.ctx != nil && o.ctx.Err() != nil {
		o.done <- o.ctx.Err()
		return
	}
	e.mu.Lock()
	e.busy = o.name
	e.mu.Unlock()
	a := &Actor{e: e, ctx: o.ctx}
	err := func() (err error) {
		defer func() {
			if r := recover(); r != nil {
				err = fmt.Errorf("%s: %v", o.name, r)
			}
		}()
		return o.run(a)
	}()
	// never leave anything held, whatever happened
	a.releaseEverything()
	if err == nil && a.err != nil {
		err = a.err
	}
	e.mu.Lock()
	e.busy = ""
	e.mu.Unlock()
	o.done <- err
}

// -- errors and stats ---------------------------------------------------------------------------------

func (e *Engine) fail(err error) {
	e.mu.Lock()
	e.lastErr, e.errAt = err, e.now()
	e.errCount++
	e.mu.Unlock()
	e.emit(Event{Kind: "error", Error: err.Error()})
}

func (e *Engine) dropped(n int) {
	e.mu.Lock()
	e.stats.dropped += n
	e.mu.Unlock()
	e.emit(Event{Kind: "dropped", Count: n, Error: "input dropped: the phone was busy with a scripted action"})
}

func (e *Engine) emit(ev Event) {
	e.mu.Lock()
	defer e.mu.Unlock()
	for _, ch := range e.listen {
		select {
		case ch <- ev:
		default:
		}
	}
}

// Stats summarizes the input path over the last two seconds.
type Stats struct {
	Reports    int     `json:"reports"`        // reports sent in the window
	Rate       float64 `json:"rate"`           // reports per second
	Merged     int     `json:"merged"`         // moves replaced by a newer one before they went out (in all)
	Dropped    int     `json:"dropped"`        // live input dropped because it was stale (in all)
	LatencyP50 float64 `json:"latency_p50_ms"` // from the input reaching the box to its report queued on USB, not counting the settle after a jump
	LatencyP95 float64 `json:"latency_p95_ms"`
	LatencyMax float64 `json:"latency_max_ms"`
	Errors     int     `json:"errors"`
	LastError  string  `json:"last_error,omitempty"`
	Busy       string  `json:"busy,omitempty"`
}

// StatsWindow is how far back Stats looks.
const StatsWindow = 2 * time.Second

// Stats returns the input statistics of the last StatsWindow.
func (e *Engine) Stats() Stats {
	e.mu.Lock()
	defer e.mu.Unlock()
	s := e.stats.summary(e.now())
	s.Errors = e.errCount
	if e.lastErr != nil && e.now().Sub(e.errAt) < 10*time.Second {
		s.LastError = e.lastErr.Error()
	}
	s.Busy = e.busy
	return s
}

type sample struct {
	at time.Time
	d  time.Duration
}

type statsWindow struct {
	samples []sample
	merged  int
	dropped int
}

func (w *statsWindow) add(d time.Duration) {
	now := time.Now()
	w.samples = append(w.samples, sample{now, d})
	if len(w.samples) > 8192 {
		w.prune(now)
		if len(w.samples) > 8192 {
			w.samples = w.samples[len(w.samples)-8192:]
		}
	}
}

func (w *statsWindow) prune(now time.Time) {
	i := 0
	for i < len(w.samples) && now.Sub(w.samples[i].at) > StatsWindow {
		i++
	}
	w.samples = append(w.samples[:0], w.samples[i:]...)
}

func (w *statsWindow) summary(now time.Time) Stats {
	w.prune(now)
	s := Stats{Reports: len(w.samples), Rate: math.Round(float64(len(w.samples))/StatsWindow.Seconds()*10) / 10,
		Merged: w.merged, Dropped: w.dropped}
	if n := len(w.samples); n > 0 {
		lat := make([]time.Duration, n)
		for i, x := range w.samples {
			lat[i] = x.d
		}
		sort.Slice(lat, func(i, j int) bool { return lat[i] < lat[j] })
		ms := func(d time.Duration) float64 { return math.Round(float64(d)/1e4) / 100 }
		s.LatencyP50 = ms(lat[n/2])
		s.LatencyP95 = ms(lat[(n*95)/100])
		s.LatencyMax = ms(lat[n-1])
	}
	return s
}

// -- helpers ---------------------------------------------------------------------------------------------

func clamp01(v float64) float64 {
	if v != v || v < 0 {
		return 0
	}
	if v > 1 {
		return 1
	}
	return v
}

func clampInt(v, lo, hi int) int {
	if v < lo {
		return lo
	}
	if v > hi {
		return hi
	}
	return v
}

func abs(v int) int {
	if v < 0 {
		return -v
	}
	return v
}

func copyKeys(ks hid.KeyState) hid.KeyState {
	return hid.KeyState{Mods: ks.Mods, Keys: append([]uint8(nil), ks.Keys...)}
}

// releasesOnly tells whether going from a to b only lets keys go.
func releasesOnly(a, b hid.KeyState) bool {
	if b.Mods&^a.Mods != 0 {
		return false
	}
	for _, k := range b.Keys {
		found := false
		for _, h := range a.Keys {
			if h == k {
				found = true
			}
		}
		if !found {
			return false
		}
	}
	return true
}

func intersect(a, b []uint8) []uint8 {
	var out []uint8
	for _, x := range a {
		for _, y := range b {
			if x == y {
				out = append(out, x)
				break
			}
		}
	}
	return out
}
