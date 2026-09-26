// Package server is the box's HTTP and WebSocket API and serves the web console.
//
// A box drives one iPhone. Test frameworks call the REST actions (tap, swipe, type...) and read
// screenshots; the console streams the screen over a WebSocket and sends touch and keys live over
// another one. Every API call needs the box's token, and browsers must be on the box's own origin.
package server

import (
	"crypto/subtle"
	"encoding/json"
	"errors"
	"io"
	"io/fs"
	"log"
	"net"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/pravrilgreen/iphone-hid/box/internal/hid"
	"github.com/pravrilgreen/iphone-hid/box/internal/input"
	"github.com/pravrilgreen/iphone-hid/box/internal/video"
)

// Config describes the server.
type Config struct {
	DeviceID   string   // the phone's name on the network, e.g. iphone-b40d9e
	Version    string   // the box software's version
	Token      string   // required on every API call; empty only for development (a warning is logged)
	AllowHosts []string // extra host names the box answers to (besides IPs, localhost, *.local, its hostname)
	Web        fs.FS    // the console's static files
	Log        *log.Logger
	SimState   func() any // with a simulated phone: what it shows, served at /api/devices/{id}/sim
	// with a simulated phone: POST /api/devices/{id}/sim {"usb": ..., "video": ...} sets what its cables report
	SimSet func(usb, video string) error
	// how many viewers the stream and MJPEG serve at once (0: 4)
	MaxViewers int
}

// Server is the box's API.
type Server struct {
	cfg     Config
	engine  *input.Engine
	hub     *video.Hub
	mux     *http.ServeMux
	started time.Time
	log     *log.Logger

	mu                   sync.Mutex
	last                 *ActionResult
	controller           *controlConn  // the live control connection, if any
	viewers              int           // open screen streams
	pingEvery, silentFor time.Duration // control connection liveness
}

// New builds the server around an input engine and a video hub.
func New(cfg Config, engine *input.Engine, hub *video.Hub) *Server {
	if cfg.Log == nil {
		cfg.Log = log.New(os.Stderr, "", log.LstdFlags)
	}
	s := &Server{cfg: cfg, engine: engine, hub: hub, mux: http.NewServeMux(), started: time.Now(), log: cfg.Log,
		pingEvery: pingEvery, silentFor: silentFor}
	s.routes()
	return s
}

// Handler is the server's HTTP handler, with the host and origin checks in front.
func (s *Server) Handler() http.Handler { return s.guard(s.mux) }

func (s *Server) routes() {
	m := s.mux
	m.HandleFunc("GET /api/health", s.health)
	m.HandleFunc("GET /api/openapi.json", s.openapi)
	m.Handle("GET /api/devices", s.auth(http.HandlerFunc(s.devices)))
	m.Handle("GET /api/devices/{id}", s.auth(s.device(s.status)))
	m.Handle("GET /api/devices/{id}/screenshot", s.auth(s.device(s.screenshot)))
	m.Handle("GET /api/devices/{id}/mjpeg", s.auth(s.device(s.mjpeg)))
	m.Handle("GET /api/devices/{id}/stream", s.auth(s.device(s.stream)))
	m.Handle("GET /api/devices/{id}/control", s.auth(s.device(s.control)))
	m.Handle("POST /api/devices/{id}/orientation", s.auth(s.device(s.orientation)))
	if s.cfg.SimState != nil {
		m.Handle("GET /api/devices/{id}/sim", s.auth(s.device(func(w http.ResponseWriter, _ *http.Request) {
			writeJSON(w, http.StatusOK, s.cfg.SimState())
		})))
	}
	if s.cfg.SimSet != nil {
		m.Handle("POST /api/devices/{id}/sim", s.auth(s.device(func(w http.ResponseWriter, r *http.Request) {
			var c struct {
				USB   string `json:"usb"`
				Video string `json:"video"`
			}
			if err := decode(r, &c); err != nil {
				writeError(w, http.StatusBadRequest, "bad_request", err.Error())
				return
			}
			if err := s.cfg.SimSet(c.USB, c.Video); err != nil {
				writeError(w, http.StatusBadRequest, "bad_request", err.Error())
				return
			}
			writeJSON(w, http.StatusOK, map[string]any{"ok": true})
		})))
	}
	m.Handle("POST /api/devices/{id}/{action}", s.auth(s.device(s.action)))
	// the API's other paths answer in JSON too: a wrong method, or no such endpoint
	fallback := func(w http.ResponseWriter, r *http.Request) {
		for _, method := range []string{http.MethodGet, http.MethodPost} {
			if method == r.Method {
				continue
			}
			other := r.Clone(r.Context())
			other.Method = method
			if _, pattern := m.Handler(other); !strings.HasSuffix(pattern, " /api/") && pattern != "" {
				w.Header().Set("Allow", method)
				writeError(w, http.StatusMethodNotAllowed, "method_not_allowed", r.URL.Path+" takes "+method)
				return
			}
		}
		writeError(w, http.StatusNotFound, "not_found", "no such endpoint: "+r.URL.Path+" (the API is described at /api/openapi.json)")
	}
	for _, method := range []string{"GET", "POST", "PUT", "PATCH", "DELETE"} {
		m.HandleFunc(method+" /api/", fallback)
	}
	if s.cfg.Web != nil {
		files := http.FileServer(http.FS(s.cfg.Web))
		m.Handle("GET /", http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.Header().Set("Cache-Control", "no-cache")
			files.ServeHTTP(w, r)
		}))
	}
}

// -- guards --------------------------------------------------------------------------------------------------

// guard refuses requests addressed to a host name the box does not answer to (a DNS rebinding
// page) and browser requests from another origin.
func (s *Server) guard(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		host := r.Host
		if h, _, err := net.SplitHostPort(host); err == nil {
			host = h
		}
		if !s.hostAllowed(host) {
			writeError(w, http.StatusForbidden, "bad_host", "unknown host name "+host+
				" (start the box with --allow-host to answer to it)")
			return
		}
		if origin := r.Header.Get("Origin"); origin != "" && !sameOrigin(origin, r.Host) {
			writeError(w, http.StatusForbidden, "bad_origin", "requests from other web sites are refused")
			return
		}
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Header().Set("X-Frame-Options", "DENY")
		next.ServeHTTP(w, r)
	})
}

func (s *Server) hostAllowed(host string) bool {
	host = strings.ToLower(strings.TrimSuffix(strings.Trim(host, "[]"), "."))
	if host == "" || net.ParseIP(host) != nil || host == "localhost" || strings.HasSuffix(host, ".local") {
		return true
	}
	if name, err := os.Hostname(); err == nil && strings.EqualFold(host, name) {
		return true
	}
	for _, h := range s.cfg.AllowHosts {
		if strings.EqualFold(host, h) {
			return true
		}
	}
	return false
}

func sameOrigin(origin, host string) bool {
	for _, scheme := range []string{"http://", "https://"} {
		if strings.EqualFold(origin, scheme+host) {
			return true
		}
	}
	return false
}

// auth requires the box token: "Authorization: Bearer <token>", or ?token= where headers cannot
// be set (WebSockets, <img> sources).
func (s *Server) auth(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if s.cfg.Token != "" {
			got := ""
			if scheme, tok, ok := strings.Cut(r.Header.Get("Authorization"), " "); ok && strings.EqualFold(scheme, "Bearer") {
				got = strings.TrimSpace(tok)
			}
			if got == "" {
				got = r.URL.Query().Get("token")
			}
			if subtle.ConstantTimeCompare([]byte(got), []byte(s.cfg.Token)) != 1 {
				w.Header().Set("WWW-Authenticate", `Bearer realm="ihc"`)
				writeError(w, http.StatusUnauthorized, "unauthorized", "this box needs its API token")
				return
			}
		}
		next.ServeHTTP(w, r)
	})
}

// device checks the {id} of the path.
func (s *Server) device(h http.HandlerFunc) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if id := r.PathValue("id"); id != s.cfg.DeviceID {
			writeError(w, http.StatusNotFound, "no_device", "no device "+id+" on this box (it has "+s.cfg.DeviceID+")")
			return
		}
		h(w, r)
	})
}

// -- status ------------------------------------------------------------------------------------------------

// Status is what the box reports about its phone.
type Status struct {
	ID       string          `json:"id"`
	Version  string          `json:"version"`
	State    string          `json:"state"` // ready, busy, starting, no_usb, asleep, no_video
	Message  string          `json:"message,omitempty"`
	USB      hid.Link        `json:"usb"`
	Video    video.Status    `json:"video"`
	Input    input.Stats     `json:"input"`
	Screen   ScreenInfo      `json:"screen"`
	Buttons  []input.Button  `json:"buttons"`
	Last     *ActionResult   `json:"last_action,omitempty"`
	Live     bool            `json:"live"` // an operator controls the phone live now
	Uptime   float64         `json:"uptime_s"`
	Encoder  string          `json:"encoder"`
	Settings json.RawMessage `json:"settings,omitempty"`
}

// ScreenInfo describes the picture of the phone screen the box sends.
type ScreenInfo struct {
	Width      int  `json:"width"`
	Height     int  `json:"height"`
	Landscape  bool `json:"landscape"`
	PointsWide int  `json:"points_wide"` // iPhone 15 sizes, for converting to points
	PointsHigh int  `json:"points_high"`
}

// Snapshot returns the phone's status.
func (s *Server) Snapshot() Status {
	link := s.engine.Sink().Link()
	vs := s.hub.Status()
	st := Status{ID: s.cfg.DeviceID, Version: s.cfg.Version, USB: link, Video: vs, Input: s.engine.Stats(),
		Buttons: input.Buttons, Uptime: time.Since(s.started).Round(time.Second).Seconds(), Encoder: video.EncoderName}
	layout := s.hub.Layout()
	st.Screen = ScreenInfo{Landscape: layout.Landscape, PointsWide: 393, PointsHigh: 852}
	if layout.Landscape {
		st.Screen.PointsWide, st.Screen.PointsHigh = 852, 393
	}
	if f := s.hub.Latest(); f != nil {
		st.Screen.Width, st.Screen.Height = f.Image.W, f.Image.H
	}
	s.mu.Lock()
	st.Last = s.last
	st.Live = s.controller != nil
	s.mu.Unlock()
	switch {
	case !link.Connected && link.State == "suspended":
		st.State, st.Message = "asleep", "the iPhone is asleep: wake it, and set Auto-Lock to Never"
	case !link.Connected:
		st.State, st.Message = "no_usb", "the iPhone has not connected as a keyboard and pointer ("+orNone(link.State)+
			"): check the USB cable and allow the accessory on the phone"
	case vs.State == "starting":
		st.State, st.Message = "starting", "waiting for the first picture"
	case vs.State != "ok":
		st.State, st.Message = "no_video", noPicture(vs)
	case s.engine.Busy() != "":
		st.State, st.Message = "busy", "running "+s.engine.Busy()
	default:
		st.State = "ready"
	}
	return st
}

func orNone(s string) string {
	if s == "" {
		return "no details"
	}
	return s
}

func (s *Server) health(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "version": s.cfg.Version, "devices": 1,
		"auth": s.cfg.Token != ""})
}

func (s *Server) devices(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{"devices": []Status{s.Snapshot()}})
}

func (s *Server) status(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, s.Snapshot())
}

func (s *Server) orientation(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Landscape *bool `json:"landscape"`
	}
	if err := decode(r, &body); err != nil || body.Landscape == nil {
		writeError(w, http.StatusBadRequest, "bad_request", `send {"landscape": true} or {"landscape": false}`)
		return
	}
	l := s.hub.Layout()
	l.Landscape = *body.Landscape
	s.hub.SetLayout(l)
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "landscape": l.Landscape})
}

// -- helpers ------------------------------------------------------------------------------------------------

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(code)
	enc := json.NewEncoder(w)
	enc.SetEscapeHTML(false)
	_ = enc.Encode(v)
}

func writeError(w http.ResponseWriter, code int, kind, msg string) {
	writeJSON(w, code, map[string]any{"ok": false, "code": kind, "error": msg})
}

// decode reads a JSON body of at most 64 KiB, refusing unknown fields. An empty body is {}.
func decode(r *http.Request, v any) error {
	if ct := r.Header.Get("Content-Type"); ct != "" && !strings.HasPrefix(ct, "application/json") {
		return errors.New("the body must be JSON (Content-Type: application/json)")
	}
	dec := json.NewDecoder(http.MaxBytesReader(nil, r.Body, 64<<10))
	dec.DisallowUnknownFields()
	if err := dec.Decode(v); err != nil {
		if errors.Is(err, io.EOF) {
			return nil
		}
		return errors.New(jsonError(err))
	}
	if _, err := dec.Token(); !errors.Is(err, io.EOF) {
		return errors.New("the body has more after its JSON value")
	}
	return nil
}
