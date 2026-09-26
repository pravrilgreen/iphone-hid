package input

import (
	"context"
	"errors"
	"sync"
	"testing"
	"time"

	"github.com/pravrilgreen/iphone-hid/box/internal/hid"
)

// fakeSink records reports; each write takes `poll` (the phone polls the endpoint every so often).
type fakeSink struct {
	mu      sync.Mutex
	poll    time.Duration
	reports []rec
	fail    error
}

type rec struct {
	kind string
	p    hid.Pointer
	ks   hid.KeyState
	bits uint32
	at   time.Time
}

func (s *fakeSink) add(r rec) error {
	if s.poll > 0 {
		time.Sleep(s.poll)
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.fail != nil {
		return s.fail
	}
	r.at = time.Now()
	s.reports = append(s.reports, r)
	return nil
}

func (s *fakeSink) Pointer(p hid.Pointer) error   { return s.add(rec{kind: "ptr", p: p}) }
func (s *fakeSink) Keyboard(k hid.KeyState) error { return s.add(rec{kind: "kbd", ks: copyKeys(k)}) }
func (s *fakeSink) Consumer(b uint32) error       { return s.add(rec{kind: "cons", bits: b}) }
func (s *fakeSink) Sync(time.Duration) error      { return nil }
func (s *fakeSink) Link() hid.Link                { return hid.Link{State: "configured", Connected: true} }
func (s *fakeSink) Wake(time.Duration) (string, string, error) {
	return "suspended", "configured", nil
}
func (s *fakeSink) Close() error { return nil }

func (s *fakeSink) all() []rec {
	s.mu.Lock()
	defer s.mu.Unlock()
	return append([]rec(nil), s.reports...)
}

func (s *fakeSink) pointers() []rec {
	var out []rec
	for _, r := range s.all() {
		if r.kind == "ptr" {
			out = append(out, r)
		}
	}
	return out
}

func waitFor(t *testing.T, what string, cond func() bool) {
	t.Helper()
	deadline := time.Now().Add(3 * time.Second)
	for !cond() {
		if time.Now().After(deadline) {
			t.Fatalf("timed out waiting for %s", what)
		}
		time.Sleep(2 * time.Millisecond)
	}
}

func testConfig() Config {
	c := DefaultConfig
	c.Settle = 40 * time.Millisecond
	c.TapHold = 20 * time.Millisecond
	c.ReleaseGap = 2 * time.Millisecond
	c.KeyHold = 2 * time.Millisecond
	c.KeyGap = 1 * time.Millisecond
	c.Step = 4 * time.Millisecond
	return c
}

func TestLiveMovesKeepOnlyTheNewestPositionButEveryPress(t *testing.T) {
	sink := &fakeSink{poll: 5 * time.Millisecond}
	e := New(sink, testConfig())
	defer e.Close()
	e.Live(Touch{X: 0.5, Y: 0.5})
	// a burst of hover moves while the first report is on the wire: they merge into the last one
	for i := 0; i < 50; i++ {
		e.Live(Touch{X: 0.5 + float64(i)/1000, Y: 0.5})
	}
	e.Live(Touch{X: 0.6, Y: 0.6, Buttons: 1}) // press
	e.Live(Touch{X: 0.6, Y: 0.6, Buttons: 0}) // release at once: never merged away
	waitFor(t, "the release", func() bool {
		ps := sink.pointers()
		return len(ps) > 0 && ps[len(ps)-1].p.Buttons == 0 && hasPress(ps)
	})
	ps := sink.pointers()
	if len(ps) > 10 {
		t.Fatalf("%d pointer reports for 50 merged moves", len(ps))
	}
	if !hasPress(ps) {
		t.Fatal("the press was lost")
	}
	var press rec
	for _, r := range ps {
		if r.p.Buttons == 1 {
			press = r
			break
		}
	}
	if press.p.X != hid.AbsCoord(0.6) || press.p.Y != hid.AbsCoord(0.6) {
		t.Fatalf("the press landed at %d,%d, not where it was made", press.p.X, press.p.Y)
	}
}

func hasPress(ps []rec) bool {
	for _, r := range ps {
		if r.p.Buttons&1 != 0 {
			return true
		}
	}
	return false
}

func TestAPressAfterAJumpWaitsForThePointerToSettle(t *testing.T) {
	sink := &fakeSink{}
	cfg := testConfig()
	e := New(sink, cfg)
	defer e.Close()
	// a touch screen: the press comes with a new position, no hover before it
	e.Live(Touch{X: 0.1, Y: 0.1, Buttons: 1})
	waitFor(t, "the press", func() bool { return hasPress(sink.pointers()) })
	ps := sink.pointers()
	if len(ps) < 2 || ps[0].p.Buttons != 0 || ps[1].p.Buttons != 1 {
		t.Fatalf("expected a move then a press, got %+v", ps)
	}
	if gap := ps[1].at.Sub(ps[0].at); gap < cfg.Settle-2*time.Millisecond {
		t.Fatalf("pressed %v after the jump, before the pointer settled (%v)", gap, cfg.Settle)
	}

	// a mouse hovering there already: the click goes out at once
	e.Live(Touch{X: 0.1, Y: 0.1, Buttons: 0})
	time.Sleep(cfg.Settle + 10*time.Millisecond)
	e.Live(Touch{X: 0.1005, Y: 0.1})
	e.Live(Touch{X: 0.1005, Y: 0.1, Buttons: 1})
	start := time.Now()
	waitFor(t, "the second press", func() bool {
		n := 0
		for _, r := range sink.pointers() {
			if r.p.Buttons == 1 {
				n++
			}
		}
		return n == 2
	})
	if d := time.Since(start); d > cfg.Settle/2 {
		t.Fatalf("a click without a jump waited %v", d)
	}
}

func TestStaleInputIsDroppedButReleasesGoOut(t *testing.T) {
	sink := &fakeSink{}
	cfg := testConfig()
	cfg.Stale = 30 * time.Millisecond
	e := New(sink, cfg)
	defer e.Close()
	e.Live(Touch{X: 0.5, Y: 0.5})
	e.Live(Touch{X: 0.5, Y: 0.5, Buttons: 1})
	waitFor(t, "the press", func() bool { return hasPress(sink.pointers()) })
	events, stop := e.Subscribe()
	defer stop()
	// a scripted action holds the phone; live input queues behind it and goes stale
	go e.Do(context.Background(), "hold", func(a *Actor) error { a.Sleep(80 * time.Millisecond); return nil })
	time.Sleep(5 * time.Millisecond)
	e.Live(Touch{X: 0.9, Y: 0.9, Buttons: 1}) // stale by the time it runs
	e.Live(Touch{X: 0.9, Y: 0.9, Buttons: 0}) // a release: always sent
	select {
	case ev := <-events:
		if ev.Kind != "dropped" {
			t.Fatalf("event %+v", ev)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("no dropped event")
	}
	waitFor(t, "everything released", func() bool {
		ps := sink.pointers()
		return ps[len(ps)-1].p.Buttons == 0
	})
	for _, r := range sink.pointers() {
		if r.p.X == hid.AbsCoord(0.9) && r.p.Buttons != 0 {
			t.Fatal("a stale press went out")
		}
	}
}

func TestTapSettlesPressesAndReleasesSeveralTimes(t *testing.T) {
	sink := &fakeSink{}
	cfg := testConfig()
	e := New(sink, cfg)
	defer e.Close()
	if err := e.Do(context.Background(), "tap", func(a *Actor) error { a.Tap(0.25, 0.75, 0); return nil }); err != nil {
		t.Fatal(err)
	}
	ps := sink.pointers()
	want := []uint8{0, 1, 0, 0, 0}
	if len(ps) != len(want) {
		t.Fatalf("%d reports, want %d: %+v", len(ps), len(want), ps)
	}
	for i, r := range ps {
		if r.p.Buttons != want[i] || r.p.X != hid.AbsCoord(0.25) || r.p.Y != hid.AbsCoord(0.75) {
			t.Fatalf("report %d: %+v", i, r.p)
		}
	}
	if ps[1].at.Sub(ps[0].at) < cfg.Settle-2*time.Millisecond {
		t.Fatal("tap pressed before the pointer settled")
	}
	if ps[2].at.Sub(ps[1].at) < cfg.TapHold-2*time.Millisecond {
		t.Fatal("tap released too early")
	}
}

func TestSwipeMovesInStepsAndLiftsAtTheEnd(t *testing.T) {
	sink := &fakeSink{}
	e := New(sink, testConfig())
	defer e.Close()
	if err := e.Do(context.Background(), "swipe", func(a *Actor) error {
		a.Swipe(0.5, 0.8, 0.5, 0.2, 40*time.Millisecond)
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	ps := sink.pointers()
	last := ps[len(ps)-1]
	if last.p.Buttons != 0 || last.p.Y != hid.AbsCoord(0.2) {
		t.Fatalf("did not lift at the end point: %+v", last.p)
	}
	moves := 0
	for i := 1; i < len(ps); i++ {
		if ps[i].p.Buttons == 1 && ps[i].p.Y < ps[i-1].p.Y {
			moves++
		}
	}
	if moves < 8 {
		t.Fatalf("only %d pressed moves in a 40 ms swipe at 4 ms steps", moves)
	}
}

func TestTypeRefusesUntypeableTextBeforeSendingAnything(t *testing.T) {
	sink := &fakeSink{}
	e := New(sink, testConfig())
	defer e.Close()
	err := e.Do(context.Background(), "type", func(a *Actor) error { a.Type("ok é"); return nil })
	if err == nil {
		t.Fatal("é must be refused")
	}
	for _, r := range sink.all() {
		if r.kind == "kbd" && len(r.ks.Keys) > 0 {
			t.Fatal("a key went out before the text was checked")
		}
	}
	if err := e.Do(context.Background(), "type", func(a *Actor) error { a.Type("ab"); return nil }); err != nil {
		t.Fatal(err)
	}
	var keys []uint8
	for _, r := range sink.all() {
		if r.kind == "kbd" && len(r.ks.Keys) > 0 {
			keys = append(keys, r.ks.Keys[0])
		}
	}
	if len(keys) != 2 || keys[0] != 0x04 || keys[1] != 0x05 {
		t.Fatalf("typed %v", keys)
	}
}

func TestAFailedActionStillReleasesEverything(t *testing.T) {
	sink := &fakeSink{}
	e := New(sink, testConfig())
	defer e.Close()
	boom := errors.New("boom")
	err := e.Do(context.Background(), "broken", func(a *Actor) error {
		a.Move(0.5, 0.5)
		a.Buttons(hid.ButtonPrimary)
		a.Key(hid.KeyState{Mods: hid.ModShift}, time.Millisecond)
		return boom
	})
	if !errors.Is(err, boom) {
		t.Fatalf("error %v", err)
	}
	ps := sink.pointers()
	if ps[len(ps)-1].p.Buttons != 0 {
		t.Fatal("the press was left down")
	}
}

func TestButtonsUseTheirMechanism(t *testing.T) {
	sink := &fakeSink{}
	e := New(sink, testConfig())
	defer e.Close()
	for _, name := range []string{"home", "volume_up", "app_switcher"} {
		if err := e.Do(context.Background(), name, func(a *Actor) error { a.PressButton(name); return nil }); err != nil {
			t.Fatal(err)
		}
	}
	var sawCmdH, sawVolume, sawMiddle bool
	for _, r := range sink.all() {
		switch {
		case r.kind == "kbd" && r.ks.Mods == hid.ModCmd && len(r.ks.Keys) == 1 && r.ks.Keys[0] == 0x0B:
			sawCmdH = true
		case r.kind == "cons" && r.bits == hid.ConsumerKeys["volume_up"]:
			sawVolume = true
		case r.kind == "ptr" && r.p.Buttons == hid.ButtonMiddle:
			sawMiddle = true
		}
	}
	if !sawCmdH || !sawVolume || !sawMiddle {
		t.Fatalf("home %v volume %v app switcher %v", sawCmdH, sawVolume, sawMiddle)
	}
	if err := e.Do(context.Background(), "x", func(a *Actor) error { a.PressButton("nope"); return nil }); err == nil {
		t.Fatal("an unknown button must fail")
	}
}

func TestStatsReportLatency(t *testing.T) {
	sink := &fakeSink{poll: time.Millisecond}
	e := New(sink, testConfig())
	defer e.Close()
	for i := 0; i < 20; i++ {
		e.Live(Touch{X: float64(i) / 20, Y: 0.5})
		time.Sleep(2 * time.Millisecond)
	}
	time.Sleep(20 * time.Millisecond)
	s := e.Stats()
	if s.Reports == 0 || s.LatencyP50 <= 0 || s.LatencyMax < s.LatencyP50 {
		t.Fatalf("stats %+v", s)
	}
}

func TestHomeCanUseTheSecondaryButton(t *testing.T) {
	if err := SetHomeMethod("button"); err != nil {
		t.Fatal(err)
	}
	defer SetHomeMethod("keys")
	sink := &fakeSink{}
	e := New(sink, testConfig())
	defer e.Close()
	if err := e.Do(context.Background(), "home", func(a *Actor) error { a.PressButton("home"); return nil }); err != nil {
		t.Fatal(err)
	}
	found := false
	for _, r := range sink.all() {
		if r.kind == "kbd" && len(r.ks.Keys) > 0 {
			t.Fatal("Home sent keys with the button method")
		}
		if r.kind == "ptr" && r.p.Buttons == hid.ButtonSecondary {
			found = true
		}
	}
	if !found {
		t.Fatal("no secondary button press")
	}
	if SetHomeMethod("gesture") == nil {
		t.Fatal("an unknown method must be refused")
	}
}

func TestStatsLeaveOutTheSettleWait(t *testing.T) {
	sink := &fakeSink{}
	cfg := testConfig()
	e := New(sink, cfg)
	defer e.Close()
	e.Live(Touch{X: 0.9, Y: 0.9, Buttons: 1}) // a jump, then a press: the press waits cfg.Settle
	waitFor(t, "the press", func() bool { return hasPress(sink.pointers()) })
	if s := e.Stats(); s.LatencyMax >= float64(cfg.Settle.Milliseconds()) {
		t.Fatalf("the deliberate settle counted as latency: %+v", s)
	}
}

func TestAFreshEngineNeverSendsThePointerToTheCorner(t *testing.T) {
	sink := &fakeSink{}
	e := New(sink, testConfig())
	defer e.Close()
	ctx := context.Background()
	if err := e.Do(ctx, "key", func(a *Actor) error { a.Key(hid.KeyState{Keys: []uint8{0x04}}, 0); return a.Err() }); err != nil {
		t.Fatal(err)
	}
	if err := e.Do(ctx, "click", func(a *Actor) error { a.PointerButton(hid.ButtonMiddle); return a.Err() }); err != nil {
		t.Fatal(err)
	}
	ps := sink.pointers()
	if len(ps) == 0 {
		t.Fatal("no pointer report: a button left down by an earlier run would stay down")
	}
	pressed := false
	for _, r := range ps {
		if r.p.X == 0 && r.p.Y == 0 {
			t.Fatalf("a report at the corner: %+v", ps)
		}
		if r.p.Buttons&hid.ButtonMiddle != 0 {
			pressed = true
			if r.p.X != hid.AbsCoord(0.5) || r.p.Y != hid.AbsCoord(0.5) {
				t.Fatalf("the middle button went down away from the centre: %+v", r.p)
			}
		}
	}
	if !pressed {
		t.Fatalf("no middle press: %+v", ps)
	}
}

// blockingSink blocks the first pointer write until released, so live input queues up.
type blockingSink struct {
	fakeSink
	once sync.Once
	gate chan struct{}
}

func (s *blockingSink) Pointer(p hid.Pointer) error {
	s.once.Do(func() { <-s.gate })
	return s.fakeSink.Pointer(p)
}

// A drag made while the engine is behind: the press goes out where it was made, not where the drag
// moved to while it waited.
func TestAPressIsNotMovedByTheMovesAfterIt(t *testing.T) {
	sink := &blockingSink{gate: make(chan struct{})}
	cfg := testConfig()
	cfg.Settle = 0
	e := New(sink, cfg)
	defer e.Close()
	e.Live(Touch{X: 0.2, Y: 0.2})             // hover (in flight, blocked)
	time.Sleep(10 * time.Millisecond)         // the engine took it
	e.Live(Touch{X: 0.2, Y: 0.2})             // hover, queued
	e.Live(Touch{X: 0.2, Y: 0.2, Buttons: 1}) // press at 0.2
	for i := 1; i <= 10; i++ {                // drag to 0.8 with the button held
		e.Live(Touch{X: 0.2 + 0.06*float64(i), Y: 0.2, Buttons: 1})
	}
	e.Live(Touch{X: 0.8, Y: 0.2, Buttons: 0}) // release
	close(sink.gate)
	waitFor(t, "release", func() bool {
		ps := sink.pointers()
		return len(ps) >= 3 && ps[len(ps)-1].p.Buttons == 0 && hasPress(ps)
	})
	for _, r := range sink.pointers() {
		t.Logf("report buttons=%d x=%.3f", r.p.Buttons, float64(r.p.X)/hid.AbsMax)
	}
	for _, r := range sink.pointers() {
		if r.p.Buttons == 1 {
			if r.p.X != hid.AbsCoord(0.2) {
				t.Fatalf("the press went out at x=%.3f, not at 0.2 where it was made", float64(r.p.X)/hid.AbsMax)
			}
			return
		}
	}
}

// A click then a quick hover away while the engine is behind: the release goes out where the press
// was, so the phone sees a tap, not a drag.
func TestAReleaseIsNotMovedByTheHoverAfterIt(t *testing.T) {
	sink := &blockingSink{gate: make(chan struct{})}
	cfg := testConfig()
	cfg.Settle = 0
	e := New(sink, cfg)
	defer e.Close()
	e.Live(Touch{X: 0.5, Y: 0.5})
	time.Sleep(10 * time.Millisecond)
	e.Live(Touch{X: 0.5, Y: 0.5, Buttons: 1})
	e.Live(Touch{X: 0.5, Y: 0.5, Buttons: 0})
	e.Live(Touch{X: 0.9, Y: 0.9, Buttons: 0}) // hover away
	close(sink.gate)
	waitFor(t, "release", func() bool {
		ps := sink.pointers()
		return len(ps) >= 3 && ps[len(ps)-1].p.Buttons == 0 && hasPress(ps)
	})
	ps := sink.pointers()
	for _, r := range ps {
		t.Logf("report buttons=%d x=%.3f", r.p.Buttons, float64(r.p.X)/hid.AbsMax)
	}
	for i, r := range ps {
		if r.p.Buttons == 1 && i+1 < len(ps) && ps[i+1].p.X != r.p.X {
			t.Fatalf("pressed at x=%.3f, released at x=%.3f in the same report", float64(r.p.X)/hid.AbsMax,
				float64(ps[i+1].p.X)/hid.AbsMax)
		}
	}
}

// Hover after a jump: small moves do not start a glide, so a click long after the jump goes out at
// once.
func TestHoverDoesNotExtendTheSettle(t *testing.T) {
	sink := &fakeSink{}
	cfg := testConfig()
	cfg.Settle = 80 * time.Millisecond
	e := New(sink, cfg)
	defer e.Close()
	e.Live(Touch{X: 0.1, Y: 0.1}) // first report: a jump
	// hover small moves every 10 ms for 300 ms
	for i := 0; i < 30; i++ {
		time.Sleep(10 * time.Millisecond)
		e.Live(Touch{X: 0.1 + float64(i)*0.0005, Y: 0.1})
	}
	x := 0.1 + 29*0.0005
	start := time.Now()
	e.Live(Touch{X: x, Y: 0.1, Buttons: 1})
	waitFor(t, "press", func() bool { return hasPress(sink.pointers()) })
	if d := time.Since(start); d > 40*time.Millisecond {
		t.Fatalf("a hover click (no jump for 300 ms, moves of 0.05%%) waited %v", d)
	}
}

func TestAScriptStartsWithTheOperatorsFingerLifted(t *testing.T) {
	sink := &fakeSink{}
	cfg := testConfig()
	e := New(sink, cfg)
	defer e.Close()
	e.Live(Touch{X: 0.9, Y: 0.5, Buttons: 1}) // an operator's finger down
	waitFor(t, "the live press", func() bool { return hasPress(sink.pointers()) })
	if err := e.Do(context.Background(), "tap", func(a *Actor) error { a.Tap(0.1, 0.5, 0); return a.Err() }); err != nil {
		t.Fatal(err)
	}
	down, lifted := false, false
	for _, r := range sink.pointers() {
		switch {
		case !down:
			down = r.p.Buttons != 0 // from the operator's press on
		case r.p.Buttons == 0:
			lifted = true
		case r.p.X != hid.AbsCoord(0.9) && !lifted:
			t.Fatalf("the finger moved to x=%.2f without lifting: the tap became a swipe", float64(r.p.X)/hid.AbsMax)
		}
	}
	if !lifted {
		t.Fatal("no release")
	}
}
