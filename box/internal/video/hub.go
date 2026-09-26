package video

import (
	"context"
	"errors"
	"math"
	"sync"
	"time"
)

// Frame is the phone's screen from one captured frame.
type Frame struct {
	Seq     uint64
	At      time.Time // when it was captured
	Image   *Image
	Source  Rect // where the phone screen sits in the captured frame
	FrameW  int  // size of the captured frame
	FrameH  int
	jpeg    map[int][]byte // quality -> encoded, filled on demand
	jpegErr error
	mu      sync.Mutex
}

// Source produces frames. Run blocks until ctx ends or the source fails; each frame goes to emit,
// which must copy what it keeps.
type Source interface {
	Run(ctx context.Context, emit func(*Raw)) error
	Describe() string
}

// Layout says where the phone screen is in a captured frame.
type Layout struct {
	Landscape bool
	Override  *Rect   // a fixed rectangle, for a capture path that crops or overscans
	Aspect    float64 // phone width / height in portrait; 0: the iPhone 15's 393 / 852
}

// PhoneAspect is the portrait aspect of current iPhones (393 x 852 points; the others are within 0.3%).
const PhoneAspect = 393.0 / 852.0

// Rect places the phone screen in a w x h frame: scaled to fit and centred, as the iPhone's HDMI
// mirror fills a video mode (black bars at the sides in portrait).
func (l Layout) Rect(w, h int) Rect {
	if l.Override != nil {
		return *l.Override
	}
	a := l.Aspect
	if a <= 0 {
		a = PhoneAspect
	}
	if l.Landscape {
		a = 1 / a
	}
	sw, sh := float64(w), float64(h)
	if sw/sh > a {
		sw = sh * a
	} else {
		sh = sw / a
	}
	rw, rh := int(math.Round(sw))&^1, int(math.Round(sh))&^1
	// even offsets and sizes: the chroma of 4:2:0 frames covers 2 x 2 pixels
	return Rect{X: ((w - rw) / 2) &^ 1, Y: ((h - rh) / 2) &^ 1, W: rw, H: rh}
}

// Status is the capture's state for the status API and the console.
type Status struct {
	State     string  `json:"state"` // starting, ok, no_signal, error
	Error     string  `json:"error,omitempty"`
	Source    string  `json:"source"`
	Format    string  `json:"format,omitempty"`
	Width     int     `json:"width,omitempty"`
	Height    int     `json:"height,omitempty"`
	FPS       float64 `json:"fps"`
	Screen    *Rect   `json:"screen,omitempty"`
	Landscape bool    `json:"landscape"`
	Encoder   string  `json:"encoder"`
	Frames    uint64  `json:"frames"`
}

// Hub runs a Source, keeps the newest frame and hands it to viewers, encoding JPEG on demand
// (once per frame and quality, whoever asks first).
type Hub struct {
	src Source

	mu      sync.Mutex
	layout  Layout
	latest  *Frame
	waiters []chan struct{}
	status  Status
	times   []time.Time

	encMu sync.Mutex
	encs  []Encoder
}

// NewHub prepares a hub for src; call Run to start capturing.
func NewHub(src Source, layout Layout) *Hub {
	return &Hub{src: src, layout: layout, status: Status{State: "starting", Source: src.Describe(), Encoder: EncoderName}}
}

// SetLayout changes where the phone screen is taken from (orientation or a fixed rectangle).
func (h *Hub) SetLayout(l Layout) {
	h.mu.Lock()
	h.layout = l
	h.mu.Unlock()
}

// Layout returns the current layout.
func (h *Hub) Layout() Layout {
	h.mu.Lock()
	defer h.mu.Unlock()
	return h.layout
}

// Run captures until ctx ends, restarting the source after failures with a growing pause (a
// phone unplugged, the signal lost, the resolution changed).
func (h *Hub) Run(ctx context.Context) {
	backoff := 250 * time.Millisecond
	for ctx.Err() == nil {
		started := time.Now()
		err := h.src.Run(ctx, h.emit)
		if ctx.Err() != nil {
			return
		}
		h.mu.Lock()
		h.status.State = "error"
		if errors.Is(err, ErrNoSignal) {
			h.status.State = "no_signal"
		}
		if err != nil {
			h.status.Error = err.Error()
		}
		h.status.FPS = 0
		h.mu.Unlock()
		if time.Since(started) > 5*time.Second {
			backoff = 250 * time.Millisecond
		}
		select {
		case <-ctx.Done():
			return
		case <-time.After(backoff):
		}
		if backoff < 4*time.Second {
			backoff *= 2
		}
	}
}

func (h *Hub) emit(raw *Raw) {
	h.mu.Lock()
	layout := h.layout
	h.mu.Unlock()
	r := layout.Rect(raw.W, raw.H).Align(SubsamplingOf(raw.Format), raw.W, raw.H)
	img, err := Crop(raw, r, nil) // a fresh image: viewers may still be encoding the previous one
	now := time.Now()
	h.mu.Lock()
	defer h.mu.Unlock()
	if err != nil {
		h.status.State, h.status.Error = "error", err.Error()
		return
	}
	var seq uint64 = 1
	if h.latest != nil {
		seq = h.latest.Seq + 1
	}
	f := &Frame{Seq: seq, At: now, Image: img, Source: r, FrameW: raw.W, FrameH: raw.H}
	h.latest = f
	h.times = append(h.times, now)
	for len(h.times) > 0 && now.Sub(h.times[0]) > 2*time.Second {
		h.times = h.times[1:]
	}
	fps := 0.0
	if n := len(h.times); n > 1 {
		fps = float64(n-1) / h.times[n-1].Sub(h.times[0]).Seconds()
	}
	h.status = Status{State: "ok", Source: h.src.Describe(), Format: raw.Format, Width: raw.W, Height: raw.H,
		FPS: math.Round(fps*10) / 10, Screen: &r, Landscape: layout.Landscape, Encoder: EncoderName, Frames: seq}
	for _, w := range h.waiters {
		close(w)
	}
	h.waiters = nil
}

// Status returns the capture state.
func (h *Hub) Status() Status {
	h.mu.Lock()
	defer h.mu.Unlock()
	s := h.status
	if s.State == "ok" && h.latest != nil && time.Since(h.latest.At) > 2*time.Second {
		s.State, s.Error, s.FPS = "stalled", "no frame for "+time.Since(h.latest.At).Round(time.Second).String(), 0
	}
	return s
}

// Latest returns the newest frame (nil before the first).
func (h *Hub) Latest() *Frame {
	h.mu.Lock()
	defer h.mu.Unlock()
	return h.latest
}

// Next waits for a frame newer than seq.
func (h *Hub) Next(ctx context.Context, seq uint64) (*Frame, error) {
	for {
		h.mu.Lock()
		if h.latest != nil && h.latest.Seq > seq {
			f := h.latest
			h.mu.Unlock()
			return f, nil
		}
		ch := make(chan struct{})
		h.waiters = append(h.waiters, ch)
		h.mu.Unlock()
		select {
		case <-ch:
		case <-ctx.Done():
			return nil, ctx.Err()
		}
	}
}

// JPEG returns the frame encoded at quality, encoding it the first time it is asked for.
func (h *Hub) JPEG(f *Frame, quality int) ([]byte, error) {
	if quality < 10 || quality > 100 {
		quality = 80
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	if b, ok := f.jpeg[quality]; ok {
		return b, nil
	}
	enc := h.encoder()
	defer h.release(enc)
	b, err := enc.Encode(f.Image, quality)
	if err != nil {
		return nil, err
	}
	if f.jpeg == nil {
		f.jpeg = map[int][]byte{}
	}
	f.jpeg[quality] = b
	return b, nil
}

func (h *Hub) encoder() Encoder {
	h.encMu.Lock()
	defer h.encMu.Unlock()
	if n := len(h.encs); n > 0 {
		e := h.encs[n-1]
		h.encs = h.encs[:n-1]
		return e
	}
	return NewEncoder()
}

func (h *Hub) release(e Encoder) {
	h.encMu.Lock()
	h.encs = append(h.encs, e)
	h.encMu.Unlock()
}
