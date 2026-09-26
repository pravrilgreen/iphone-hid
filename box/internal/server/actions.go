package server

import (
	"context"
	"errors"
	"fmt"
	"math"
	"net/http"
	"time"

	"github.com/pravrilgreen/iphone-hid/box/internal/hid"
	"github.com/pravrilgreen/iphone-hid/box/internal/input"
)

// ActionResult is the outcome of the last scripted action.
type ActionResult struct {
	Action string    `json:"action"`
	OK     bool      `json:"ok"`
	Error  string    `json:"error,omitempty"`
	Ms     float64   `json:"ms"`
	At     time.Time `json:"at"`
}

// Params are the parameters of every action; each action reads the ones it needs. Coordinates are
// fractions of the phone screen: (0, 0) top left, (1, 1) bottom right.
type Params struct {
	X          *float64 `json:"x,omitempty"`
	Y          *float64 `json:"y,omitempty"`
	X1         *float64 `json:"x1,omitempty"`
	Y1         *float64 `json:"y1,omitempty"`
	X2         *float64 `json:"x2,omitempty"`
	Y2         *float64 `json:"y2,omitempty"`
	HoldMs     *int     `json:"hold_ms,omitempty"`
	DurationMs *int     `json:"duration_ms,omitempty"`
	RestMs     *int     `json:"rest_ms,omitempty"`
	Lines      *int     `json:"lines,omitempty"`
	Amount     *int     `json:"amount,omitempty"` // older name of lines
	Text       *string  `json:"text,omitempty"`
	Combo      *string  `json:"combo,omitempty"`
	Name       *string  `json:"name,omitempty"`
	Key        *string  `json:"key,omitempty"`
	URL        *string  `json:"url,omitempty"`
}

type badRequest struct{ msg string }

func (e badRequest) Error() string { return e.msg }

func need(v *float64, name string) float64 {
	if v == nil {
		panic(badRequest{name + " is required (a fraction of the screen, 0 to 1)"})
	}
	if math.IsNaN(*v) || *v < 0 || *v > 1 {
		panic(badRequest{fmt.Sprintf("%s must be between 0 and 1 (a fraction of the screen), not %v", name, *v)})
	}
	return *v
}

func ms(v *int, def, max time.Duration, name string) time.Duration {
	if v == nil {
		return def
	}
	d := time.Duration(*v) * time.Millisecond
	if d < 0 || d > max {
		panic(badRequest{fmt.Sprintf("%s must be between 0 and %d", name, max.Milliseconds())})
	}
	return d
}

func str(v *string, name string) string {
	if v == nil || *v == "" {
		panic(badRequest{name + " is required"})
	}
	return *v
}

// actions maps an action name to a function that validates its parameters (panicking with
// badRequest) and returns what to run.
var actions = map[string]func(p Params) func(*input.Actor){
	"tap": func(p Params) func(*input.Actor) {
		x, y := need(p.X, "x"), need(p.Y, "y")
		hold := ms(p.HoldMs, 0, 10*time.Second, "hold_ms")
		return func(a *input.Actor) { a.Tap(x, y, hold) }
	},
	"long_press": func(p Params) func(*input.Actor) {
		x, y := need(p.X, "x"), need(p.Y, "y")
		d := ms(p.DurationMs, input.DefaultConfig.LongPress, 10*time.Second, "duration_ms")
		return func(a *input.Actor) { a.Tap(x, y, d) }
	},
	"swipe": func(p Params) func(*input.Actor) {
		x1, y1, x2, y2 := need(p.X1, "x1"), need(p.Y1, "y1"), need(p.X2, "x2"), need(p.Y2, "y2")
		d := ms(p.DurationMs, 250*time.Millisecond, 10*time.Second, "duration_ms")
		return func(a *input.Actor) { a.Swipe(x1, y1, x2, y2, d) }
	},
	"drag": func(p Params) func(*input.Actor) {
		x1, y1, x2, y2 := need(p.X1, "x1"), need(p.Y1, "y1"), need(p.X2, "x2"), need(p.Y2, "y2")
		hold := ms(p.HoldMs, 600*time.Millisecond, 10*time.Second, "hold_ms")
		d := ms(p.DurationMs, 600*time.Millisecond, 10*time.Second, "duration_ms")
		rest := ms(p.RestMs, 200*time.Millisecond, 10*time.Second, "rest_ms")
		return func(a *input.Actor) { a.Drag(x1, y1, x2, y2, hold, d, rest) }
	},
	"scroll": func(p Params) func(*input.Actor) {
		x, y := need(p.X, "x"), need(p.Y, "y")
		lines := p.Lines
		if lines == nil {
			lines = p.Amount
		}
		if lines == nil || *lines == 0 || *lines < -100 || *lines > 100 {
			panic(badRequest{"lines is required: -100 to 100, positive scrolls towards the top"})
		}
		n := *lines
		return func(a *input.Actor) { a.Scroll(x, y, n) }
	},
	"type": func(p Params) func(*input.Actor) {
		text := str(p.Text, "text")
		if len(text) > 4000 {
			panic(badRequest{"text is longer than 4000 characters"})
		}
		if _, err := hid.TextPresses(text); err != nil {
			panic(badRequest{err.Error()})
		}
		return func(a *input.Actor) { a.Type(text) }
	},
	"key": func(p Params) func(*input.Actor) {
		combo := str(p.Combo, "combo")
		if _, err := hid.ParseCombo(combo); err != nil {
			panic(badRequest{err.Error()})
		}
		return func(a *input.Actor) { a.Combo(combo) }
	},
	"button": func(p Params) func(*input.Actor) {
		name := str(p.Name, "name")
		if _, ok := input.FindButton(name); !ok {
			panic(badRequest{"unknown button " + name})
		}
		return func(a *input.Actor) { a.PressButton(name) }
	},
	"media": func(p Params) func(*input.Actor) {
		key := str(p.Key, "key")
		bits, ok := hid.ConsumerKeys[key]
		if !ok {
			panic(badRequest{"unknown media key " + key})
		}
		return func(a *input.Actor) { a.Consumer(bits, 0) }
	},
	"open_url": func(p Params) func(*input.Actor) {
		url := str(p.URL, "url")
		if _, err := hid.TextPresses(url); err != nil {
			panic(badRequest{err.Error()})
		}
		return func(a *input.Actor) {
			a.Combo("cmd+space")
			a.Sleep(700 * time.Millisecond)
			a.Type(url)
			a.Sleep(300 * time.Millisecond)
			a.Combo("enter")
		}
	},
	"release_all": func(Params) func(*input.Actor) { return func(*input.Actor) {} },
}

func init() {
	// the phone buttons also have an action of their own: POST .../home
	for _, b := range input.Buttons {
		name := b.Name
		actions[name] = func(Params) func(*input.Actor) { return func(a *input.Actor) { a.PressButton(name) } }
	}
}

// ActionNames lists the actions of the API.
func ActionNames() []string {
	var out []string
	for n := range actions {
		out = append(out, n)
	}
	return out
}

// Run validates and runs an action, recording its result. The error is badRequest for invalid
// parameters.
func (s *Server) Run(ctx context.Context, name string, p Params) (res ActionResult, err error) {
	build, ok := actions[name]
	if name == "wake" {
		return s.wake(ctx)
	}
	if !ok {
		return ActionResult{}, badRequest{"unknown action " + name}
	}
	var run func(*input.Actor)
	func() {
		defer func() {
			if r := recover(); r != nil {
				if br, ok := r.(badRequest); ok {
					err = br
					return
				}
				panic(r)
			}
		}()
		run = build(p)
	}()
	if err != nil {
		return ActionResult{}, err
	}
	start := time.Now()
	err = s.engine.Do(ctx, name, func(a *input.Actor) error { run(a); return a.Err() })
	res = ActionResult{Action: name, OK: err == nil, Ms: float64(time.Since(start).Microseconds()) / 1000, At: start}
	if err != nil {
		res.Error = err.Error()
	}
	s.mu.Lock()
	s.last = &res
	s.mu.Unlock()
	return res, err
}

// wake sends USB remote wakeup to a phone that suspended the bus, then a Shift press so the screen
// lights up.
func (s *Server) wake(ctx context.Context) (ActionResult, error) {
	start := time.Now()
	before, after, err := s.engine.Sink().Wake(3 * time.Second)
	if err == nil {
		err = s.engine.Do(ctx, "wake", func(a *input.Actor) error {
			a.Key(hid.KeyState{Mods: hid.ModShift}, 50*time.Millisecond)
			return a.Err()
		})
	}
	res := ActionResult{Action: "wake", OK: err == nil, Ms: float64(time.Since(start).Microseconds()) / 1000, At: start}
	if err != nil {
		res.Error = fmt.Sprintf("%v (USB %s -> %s)", err, orNone(before), orNone(after))
	}
	s.mu.Lock()
	s.last = &res
	s.mu.Unlock()
	return res, err
}

func (s *Server) action(w http.ResponseWriter, r *http.Request) {
	var p Params
	if err := decode(r, &p); err != nil {
		writeError(w, http.StatusBadRequest, "bad_request", err.Error())
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 60*time.Second)
	defer cancel()
	res, err := s.Run(ctx, r.PathValue("action"), p)
	var br badRequest
	switch {
	case errors.As(err, &br):
		code := http.StatusBadRequest
		if res.Action == "" && actions[r.PathValue("action")] == nil && r.PathValue("action") != "wake" {
			code = http.StatusNotFound
		}
		writeError(w, code, "bad_request", br.msg)
	case errors.Is(err, hid.ErrNotConnected):
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{"ok": false, "code": "no_usb", "error": err.Error(), "result": res})
	case errors.Is(err, hid.ErrNotTaken):
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{"ok": false, "code": "asleep", "error": err.Error(), "result": res})
	case err != nil:
		writeJSON(w, http.StatusInternalServerError, map[string]any{"ok": false, "code": "failed", "error": err.Error(), "result": res})
	default:
		writeJSON(w, http.StatusOK, map[string]any{"ok": true, "result": res})
	}
}
