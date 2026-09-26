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
