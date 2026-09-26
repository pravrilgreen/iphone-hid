package sim

import (
	"context"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/pravrilgreen/iphone-hid/box/internal/hid"
	"github.com/pravrilgreen/iphone-hid/box/internal/video"
)

// snapshot renders the phone and encodes it the way the box does; with IHC_SNAPSHOT_DIR set, the
// JPEG is saved there for a look.
func snapshot(t *testing.T, p *Phone, name string) []byte {
	t.Helper()
	p.mu.Lock()
	v := p.view()
	p.mu.Unlock()
	raw := p.render.frame(v, time.Date(2026, 9, 26, 9, 41, 0, 0, time.UTC))
	img, err := video.Crop(raw, video.Layout{}.Rect(raw.W, raw.H), nil)
	if err != nil {
		t.Fatal(err)
	}
	enc := video.NewEncoder()
	defer enc.Close()
	b, err := enc.Encode(img, 85)
	if err != nil {
		t.Fatal(err)
	}
	if dir := os.Getenv("IHC_SNAPSHOT_DIR"); dir != "" {
		if err := os.WriteFile(filepath.Join(dir, name+".jpg"), b, 0o644); err != nil {
			t.Fatal(err)
		}
	}
	return b
}

func at(x, y float64, buttons uint8) hid.Pointer {
	return hid.Pointer{X: hid.AbsCoord(x / ScreenW), Y: hid.AbsCoord(y / ScreenH), Buttons: buttons}
}

func TestTapOpensAnAppAndHomeCloses(t *testing.T) {
	p := New()
	snapshot(t, p, "home")
	x, y := iconPos(1) // Notes
	_ = p.Pointer(at(x+30, y+30, 0))
	_ = p.Pointer(at(x+30, y+30, 1))
	_ = p.Pointer(at(x+30, y+30, 0))
	if st := p.State(); st.Screen != "app" || st.App != "Notes" {
		t.Fatalf("state %+v", st)
	}
	for _, ks := range []hid.KeyState{{Mods: hid.ModShift, Keys: []uint8{0x0B}}, {}, {Keys: []uint8{0x0C}}, {}} {
		_ = p.Keyboard(ks)
	}
	if st := p.State(); st.Notes != "Hi" {
		t.Fatalf("notes %q", st.Notes)
	}
	snapshot(t, p, "notes")
	_ = p.Keyboard(hid.KeyState{Mods: hid.ModCmd, Keys: []uint8{hid.Keys["h"]}})
	if p.State().Screen != "home" {
		t.Fatal("Cmd+H did not go home")
	}
}

func TestDragScrollsWithMomentumAndSwipeFlipsPages(t *testing.T) {
	p := New()
	x, y := iconPos(0) // Settings
	_ = p.Pointer(at(x+30, y+30, 1))
	_ = p.Pointer(at(x+30, y+30, 0))
	start := time.Unix(0, 0)
	now := start
	p.now = func() time.Time { return now }
	_ = p.Pointer(at(200, 700, 1))
	for i := 1; i <= 10; i++ {
		now = start.Add(time.Duration(i) * 10 * time.Millisecond)
		_ = p.Pointer(at(200, 700-float64(i)*30, 1))
	}
	_ = p.Pointer(at(200, 400, 0))
	scrolled := p.State().Scroll
	if scrolled < 290 {
		t.Fatalf("the list followed the finger by %.0f pt, not 300", scrolled)
	}
	p.mu.Lock()
	p.step(0.2)
	p.mu.Unlock()
	if p.State().Scroll <= scrolled {
		t.Fatal("no momentum after a fling")
	}
	snapshot(t, p, "settings")

	_ = p.Keyboard(hid.KeyState{Mods: hid.ModCmd, Keys: []uint8{hid.Keys["h"]}})
	_ = p.Keyboard(hid.KeyState{})
	_ = p.Pointer(at(350, 400, 1))
	for i := 1; i <= 10; i++ {
		now = now.Add(10 * time.Millisecond)
		_ = p.Pointer(at(350-float64(i)*25, 400, 1))
	}
	_ = p.Pointer(at(100, 400, 0))
	for i := 0; i < 60; i++ {
		p.mu.Lock()
		p.step(1.0 / 30)
		p.mu.Unlock()
	}
	if p.State().Page != 1 {
		t.Fatalf("page %d after a swipe left", p.State().Page)
	}
}

func TestVolumeSearchAndSwitcher(t *testing.T) {
	p := New()
	_ = p.Consumer(hid.ConsumerKeys["volume_up"])
	_ = p.Consumer(0)
	if v := p.State().Volume; v <= 0.5 {
		t.Fatalf("volume %v", v)
	}
	snapshot(t, p, "volume")
	_ = p.Keyboard(hid.KeyState{Mods: hid.ModCmd, Keys: []uint8{hid.Keys["space"]}})
	_ = p.Keyboard(hid.KeyState{})
	for _, k := range []string{"m", "a"} {
		_ = p.Keyboard(hid.KeyState{Keys: []uint8{hid.Keys[k]}})
		_ = p.Keyboard(hid.KeyState{})
	}
	if st := p.State(); st.Screen != "search" || st.Search != "ma" {
		t.Fatalf("search %+v", st)
	}
	snapshot(t, p, "search")
	_ = p.Keyboard(hid.KeyState{Keys: []uint8{hid.Keys["enter"]}})
	if p.State().App != "Mail" {
		t.Fatalf("Return opened %q", p.State().App)
	}
	_ = p.Pointer(at(200, 400, hid.ButtonMiddle))
	_ = p.Pointer(at(200, 400, 0))
	if p.State().Screen != "switcher" {
		t.Fatal("the middle button did not open the app switcher")
	}
	snapshot(t, p, "switcher")
}

func TestFramesFlowThroughTheHub(t *testing.T) {
	p := New()
	hub := video.NewHub(p, video.Layout{})
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	go hub.Run(ctx)
	f, err := hub.Next(ctx, 0)
	if err != nil {
		t.Fatal(err)
	}
	if f.Image.W != 498 || f.Image.H != 1080 {
		t.Fatalf("frame %dx%d", f.Image.W, f.Image.H)
	}
}
