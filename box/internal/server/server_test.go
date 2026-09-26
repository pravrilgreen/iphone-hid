package server

import (
	"bytes"
	"context"
	"encoding/binary"
	"encoding/json"
	"image/jpeg"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"testing/fstest"
	"time"

	"github.com/gorilla/websocket"

	"github.com/pravrilgreen/iphone-hid/box/internal/input"
	"github.com/pravrilgreen/iphone-hid/box/internal/sim"
	"github.com/pravrilgreen/iphone-hid/box/internal/video"
)

const token = "secret-token"

type fixture struct {
	srv   *httptest.Server
	phone *sim.Phone
	base  string
}

func newFixture(t *testing.T) *fixture {
	t.Helper()
	phone := sim.New()
	cfg := input.DefaultConfig
	cfg.Settle = 20 * time.Millisecond
	cfg.TapHold = 30 * time.Millisecond
	engine := input.New(phone, cfg)
	hub := video.NewHub(phone, video.Layout{})
	ctx, cancel := context.WithCancel(context.Background())
	go hub.Run(ctx)
	s := New(Config{DeviceID: "iphone-test", Version: "test", Token: token,
		Web:      fstest.MapFS{"index.html": {Data: []byte("<!doctype html>console")}},
		SimState: func() any { return phone.State() }, SimSet: phone.Set}, engine, hub)
	ts := httptest.NewServer(s.Handler())
	t.Cleanup(func() {
		ts.Close()
		cancel()
		engine.Close()
	})
	return &fixture{srv: ts, phone: phone, base: ts.URL + "/api/devices/iphone-test"}
}

func (f *fixture) do(t *testing.T, method, url, body string, auth bool) (*http.Response, map[string]any) {
	t.Helper()
	req, _ := http.NewRequest(method, url, strings.NewReader(body))
	if body != "" {
		req.Header.Set("Content-Type", "application/json")
	}
	if auth {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	var out map[string]any
	b, _ := io.ReadAll(resp.Body)
	_ = json.Unmarshal(b, &out)
	return resp, out
}

func TestTokenHostAndOriginAreChecked(t *testing.T) {
	f := newFixture(t)
	if r, body := f.do(t, "GET", f.srv.URL+"/api/health", "", false); r.StatusCode != 200 || body["ok"] != true {
		t.Fatalf("health %d %v", r.StatusCode, body)
	}
	if r, _ := f.do(t, "GET", f.srv.URL+"/api/devices", "", false); r.StatusCode != 401 {
		t.Fatalf("no token: %d", r.StatusCode)
	}
	if r, _ := f.do(t, "GET", f.srv.URL+"/api/devices?token="+token, "", false); r.StatusCode != 200 {
		t.Fatalf("token in the query: %d", r.StatusCode)
	}
	req, _ := http.NewRequest("GET", f.srv.URL+"/api/devices", nil)
	req.Header.Set("Authorization", "Bearer "+token)
	req.Host = "attacker.example"
	if r, _ := http.DefaultClient.Do(req); r.StatusCode != 403 {
		t.Fatalf("foreign host name: %d", r.StatusCode)
	}
	req, _ = http.NewRequest("POST", f.base+"/home", nil)
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Origin", "https://attacker.example")
	if r, _ := http.DefaultClient.Do(req); r.StatusCode != 403 {
		t.Fatalf("foreign origin: %d", r.StatusCode)
	}
	req, _ = http.NewRequest("POST", f.base+"/tap", strings.NewReader("x=1&y=1"))
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	if r, _ := http.DefaultClient.Do(req); r.StatusCode != 400 {
		t.Fatalf("a form body must be refused: %d", r.StatusCode)
	}
	if r, _ := f.do(t, "GET", f.srv.URL+"/", "", false); r.StatusCode != 200 {
		t.Fatalf("console: %d", r.StatusCode)
	}
}

func waitState(t *testing.T, f *fixture, cond func(sim.State) bool) sim.State {
	t.Helper()
	deadline := time.Now().Add(3 * time.Second)
	for {
		st := f.phone.State()
		if cond(st) {
			return st
		}
		if time.Now().After(deadline) {
			t.Fatalf("phone state %+v", st)
		}
		time.Sleep(10 * time.Millisecond)
	}
}

func TestActionsDriveThePhone(t *testing.T) {
	f := newFixture(t)
	// Notes is the second icon of the first row: centre at (27+62+27+31, 90+31) pt
	r, body := f.do(t, "POST", f.base+"/tap", `{"x": 0.374, "y": 0.142}`, true)
	if r.StatusCode != 200 || body["ok"] != true {
		t.Fatalf("tap: %d %v", r.StatusCode, body)
	}
	waitState(t, f, func(s sim.State) bool { return s.App == "Notes" })
	if r, body := f.do(t, "POST", f.base+"/type", `{"text": "Hello"}`, true); r.StatusCode != 200 {
		t.Fatalf("type: %d %v", r.StatusCode, body)
	}
	waitState(t, f, func(s sim.State) bool { return s.Notes == "Hello" })
	if r, _ := f.do(t, "POST", f.base+"/home", "", true); r.StatusCode != 200 {
		t.Fatalf("home: %d", r.StatusCode)
	}
	waitState(t, f, func(s sim.State) bool { return s.Screen == "home" })
	if r, _ := f.do(t, "POST", f.base+"/button", `{"name": "volume_up"}`, true); r.StatusCode != 200 {
		t.Fatal("volume_up")
	}
	waitState(t, f, func(s sim.State) bool { return s.Volume > 0.5 })
	if r, _ := f.do(t, "POST", f.base+"/swipe", `{"x1": 0.9, "y1": 0.5, "x2": 0.1, "y2": 0.5, "duration_ms": 200}`, true); r.StatusCode != 200 {
		t.Fatal("swipe")
	}
	waitState(t, f, func(s sim.State) bool { return s.Page == 1 })

	st := f.do
	_, status := st(t, "GET", f.base, "", true)
	if status["state"] != "ready" || status["last_action"].(map[string]any)["action"] != "swipe" {
		t.Fatalf("status %v", status)
	}
	if buttons := status["buttons"].([]any); len(buttons) < 5 {
		t.Fatalf("buttons %v", buttons)
	}
}

func TestBadRequestsAreRefusedBeforeAnythingIsSent(t *testing.T) {
	f := newFixture(t)
	before := f.phone.State().Reports
	cases := map[string]string{
		"/tap":    `{"x": 1.5, "y": 0.5}`,
		"/tap2":   `{"x": 0.5, "y": 0.5}`,
		"/type":   `{"text": "café"}`,
		"/key":    `{"combo": "cmd+nope"}`,
		"/button": `{"name": "power"}`,
		"/swipe":  `{"x1": 0.5}`,
		"/scroll": `{"x": 0.5, "y": 0.5, "lines": 0}`,
	}
	for path, body := range cases {
		r, out := f.do(t, "POST", f.base+path, body, true)
		if r.StatusCode != 400 && r.StatusCode != 404 {
			t.Errorf("%s %s: %d %v", path, body, r.StatusCode, out)
		}
	}
	if r, _ := f.do(t, "POST", f.base+"/tap", `{"x": 0.5, "y": 0.5, "bogus": 1}`, true); r.StatusCode != 400 {
		t.Error("an unknown field must be refused")
	}
	if r, _ := f.do(t, "GET", f.srv.URL+"/api/devices/other", "", true); r.StatusCode != 404 {
		t.Error("another device id must be 404")
	}
	if f.phone.State().Reports != before {
		t.Fatal("a refused request sent reports")
	}
}

func TestScreenshotIsThePhoneScreen(t *testing.T) {
	f := newFixture(t)
	req, _ := http.NewRequest("GET", f.base+"/screenshot?wait=true", nil)
	req.Header.Set("Authorization", "Bearer "+token)
	r, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer r.Body.Close()
	if r.Header.Get("Content-Type") != "image/jpeg" {
		t.Fatalf("content type %q", r.Header.Get("Content-Type"))
	}
	m, err := jpeg.Decode(r.Body)
	if err != nil {
		t.Fatal(err)
	}
	if m.Bounds().Dx() != 498 || m.Bounds().Dy() != 1080 {
		t.Fatalf("screenshot %v", m.Bounds())
	}
}

func dial(t *testing.T, f *fixture, path string) (*websocket.Conn, *http.Response, error) {
	t.Helper()
	u := "ws" + strings.TrimPrefix(f.base, "http") + path
	if strings.Contains(u, "?") {
		u += "&token=" + token
	} else {
		u += "?token=" + token
	}
	return websocket.DefaultDialer.Dial(u, nil)
}

func touch(buttons uint8, x, y float64) []byte {
	b := []byte{msgTouch, buttons, 0, 0, 0, 0, 0}
	binary.LittleEndian.PutUint16(b[2:], uint16(x*65535))
	binary.LittleEndian.PutUint16(b[4:], uint16(y*65535))
	return b
}

func TestLiveControlOverTheWebSocket(t *testing.T) {
	f := newFixture(t)
	ws, _, err := dial(t, f, "/control")
	if err != nil {
		t.Fatal(err)
	}
	defer ws.Close()
	var hello map[string]any
	if err := ws.ReadJSON(&hello); err != nil || hello["t"] != "hello" {
		t.Fatalf("hello %v %v", hello, err)
	}
	// hover to Settings, press, release: a tap
	for _, m := range [][]byte{touch(0, 0.14, 0.142), touch(1, 0.14, 0.142), touch(0, 0.14, 0.142)} {
		if err := ws.WriteMessage(websocket.BinaryMessage, m); err != nil {
			t.Fatal(err)
		}
		time.Sleep(40 * time.Millisecond)
	}
	waitState(t, f, func(s sim.State) bool { return s.App == "Settings" })
	// drag the list up: it scrolls while the finger moves
	_ = ws.WriteMessage(websocket.BinaryMessage, touch(1, 0.5, 0.8))
	for i := 1; i <= 20; i++ {
		_ = ws.WriteMessage(websocket.BinaryMessage, touch(1, 0.5, 0.8-float64(i)*0.02))
		time.Sleep(5 * time.Millisecond)
	}
	waitState(t, f, func(s sim.State) bool { return s.Scroll > 250 && s.Pressed })
	_ = ws.WriteMessage(websocket.BinaryMessage, touch(0, 0.5, 0.4))

	// ping echo
	_ = ws.WriteMessage(websocket.BinaryMessage, []byte{msgPing, 7, 0, 0, 0})
	deadline := time.Now().Add(3 * time.Second)
	for {
		kind, data, err := ws.ReadMessage()
		if err != nil {
			t.Fatal(err)
		}
		if kind == websocket.BinaryMessage && bytes.Equal(data, []byte{msgPing, 7, 0, 0, 0}) {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("no pong")
		}
	}
	// an action over the socket
	_ = ws.WriteJSON(map[string]any{"t": "action", "id": 5, "action": "key", "combo": "cmd+h"})
	for {
		var msg map[string]any
		_, data, err := ws.ReadMessage()
		if err != nil {
			t.Fatal(err)
		}
		if json.Unmarshal(data, &msg) == nil && msg["t"] == "result" {
			if msg["ok"] != true || msg["id"].(float64) != 5 {
				t.Fatalf("result %v", msg)
			}
			break
		}
	}
	waitState(t, f, func(s sim.State) bool { return s.Screen == "home" })

	// a second client is refused, unless it takes over
	other, _, err := dial(t, f, "/control")
	if err != nil {
		t.Fatal(err)
	}
	if _, _, err := other.ReadMessage(); !websocket.IsCloseError(err, closeInUse) {
		t.Fatalf("second controller: %v", err)
	}
	taker, _, err := dial(t, f, "/control?takeover=true")
	if err != nil {
		t.Fatal(err)
	}
	defer taker.Close()
	for {
		if _, _, err := ws.ReadMessage(); err != nil {
			if !websocket.IsCloseError(err, closeInUse) {
				t.Fatalf("taken over: %v", err)
			}
			break
		}
	}
}

func TestStreamSendsFramesWithAHeaderAndWaitsForAcks(t *testing.T) {
	f := newFixture(t)
	ws, _, err := dial(t, f, "/stream?quality=60")
	if err != nil {
		t.Fatal(err)
	}
	defer ws.Close()
	frames, statuses := 0, 0
	var lastSeq uint32
	deadline := time.Now().Add(5 * time.Second)
	for frames < 3 {
		if time.Now().After(deadline) {
			t.Fatalf("%d frames", frames)
		}
		kind, data, err := ws.ReadMessage()
		if err != nil {
			t.Fatal(err)
		}
		if kind == websocket.TextMessage {
			statuses++
			continue
		}
		if data[0] != 'F' || data[1] != 1 {
			t.Fatalf("header % x", data[:4])
		}
		seq := binary.LittleEndian.Uint32(data[4:])
		if seq <= lastSeq {
			t.Fatalf("frame %d after %d", seq, lastSeq)
		}
		lastSeq = seq
		if _, err := jpeg.Decode(bytes.NewReader(data[12:])); err != nil {
			t.Fatal(err)
		}
		frames++
		_ = ws.WriteMessage(websocket.TextMessage, []byte("ack"))
	}
	if statuses == 0 {
		t.Fatal("no status message")
	}
}

func TestOpenAPIDescribesTheActions(t *testing.T) {
	f := newFixture(t)
	r, doc := f.do(t, "GET", f.srv.URL+"/api/openapi.json", "", false)
	if r.StatusCode != 200 {
		t.Fatal(r.StatusCode)
	}
	paths := doc["paths"].(map[string]any)
	for _, a := range []string{"tap", "swipe", "drag", "type", "button", "home", "volume_up", "wake"} {
		if _, ok := paths["/api/devices/{id}/"+a]; !ok {
			t.Errorf("no path for %s", a)
		}
	}
}

type silentSource struct{}

func (silentSource) Describe() string { return "silent" }
func (silentSource) Run(ctx context.Context, _ func(*video.Raw)) error {
	<-ctx.Done()
	return nil
}

func TestStatusSaysStartingUntilTheFirstPicture(t *testing.T) {
	phone := sim.New()
	engine := input.New(phone, input.DefaultConfig)
	defer engine.Close()
	s := New(Config{DeviceID: "d"}, engine, video.NewHub(silentSource{}, video.Layout{}))
	if st := s.Snapshot(); st.State != "starting" {
		t.Fatalf("state %q before any frame", st.State)
	}
}

// waitStatus polls the status API until it reports want.
func waitStatus(t *testing.T, f *fixture, want string) map[string]any {
	t.Helper()
	deadline := time.Now().Add(6 * time.Second)
	for {
		_, st := f.do(t, "GET", f.base, "", true)
		if st["state"] == want {
			return st
		}
		if time.Now().After(deadline) {
			t.Fatalf("state %v, want %s: %v", st["state"], want, st["message"])
		}
		time.Sleep(20 * time.Millisecond)
	}
}

func TestUnpluggedAsleepAndNoPicture(t *testing.T) {
	f := newFixture(t)
	waitStatus(t, f, "ready")
	if r, _ := f.do(t, "POST", f.base+"/sim", `{"usb": "loose"}`, true); r.StatusCode != 400 {
		t.Fatalf("an unknown condition: %d", r.StatusCode)
	}

	f.do(t, "POST", f.base+"/sim", `{"usb": "unplugged"}`, true)
	waitStatus(t, f, "no_usb")
	if r, body := f.do(t, "POST", f.base+"/home", "", true); r.StatusCode != 503 || body["code"] != "no_usb" {
		t.Fatalf("Home while unplugged: %d %v", r.StatusCode, body)
	}
	if r, body := f.do(t, "POST", f.base+"/wake", "", true); r.StatusCode != 503 || body["code"] != "no_usb" {
		t.Fatalf("wake while unplugged: %d %v", r.StatusCode, body)
	}

	f.do(t, "POST", f.base+"/sim", `{"usb": "asleep"}`, true)
	waitStatus(t, f, "asleep")
	if r, body := f.do(t, "POST", f.base+"/tap", `{"x": 0.5, "y": 0.5}`, true); r.StatusCode != 503 || body["code"] != "asleep" {
		t.Fatalf("tap while asleep: %d %v", r.StatusCode, body)
	}
	if r, body := f.do(t, "POST", f.base+"/wake", "", true); r.StatusCode != 200 {
		t.Fatalf("wake: %d %v", r.StatusCode, body)
	}
	waitStatus(t, f, "ready")

	f.do(t, "POST", f.base+"/sim", `{"video": "no_signal"}`, true)
	waitStatus(t, f, "no_video")
	if r, body := f.do(t, "GET", f.base+"/screenshot?wait=true", "", true); r.StatusCode != 503 {
		t.Fatalf("a screenshot with no picture: %d %v", r.StatusCode, body)
	}
	f.do(t, "POST", f.base+"/sim", `{"video": "ok"}`, true)
	waitStatus(t, f, "ready")
}
