package server

import (
	"context"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/gorilla/websocket"

	"github.com/pravrilgreen/iphone-hid/box/internal/hid"
	"github.com/pravrilgreen/iphone-hid/box/internal/input"
)

// Live control protocol (binary WebSocket messages, little endian), client to box:
//
//	0x01 buttons x:u16 y:u16 wheel:i8   touch: position over the screen (0..65535), buttons held
//	                                    (1 touch, 2 secondary, 4 middle), wheel lines
//	0x02 mods n keys[n]                 keyboard: modifiers and up to 6 keys held
//	0x03 bits:u24                       consumer controls held (volume, mute...)
//	0x04                                release everything
//	0x05 seq:u32                        ping: echoed at once, for the round-trip time
//
// Text messages are JSON: {"t": "action", "id": 1, "action": "tap", "x": 0.5, "y": 0.5} runs an
// action and answers {"t": "result", "id": 1, "ok": true, "result": {...}}. The actions of one
// connection run one after the other, in the order sent. The box sends {"t": "hello"}, {"t": "stats"}
// every second, and {"t": "error"} or {"t": "dropped"}. It pings the client every 2 s and drops a
// connection that has answered nothing (pong or message) for 6 s.
const (
	msgTouch    = 0x01
	msgKeys     = 0x02
	msgConsumer = 0x03
	msgRelease  = 0x04
	msgPing     = 0x05
)

// Close codes and timings.
const (
	closeInUse    = 4409
	closeTooMany  = 4429
	maxViewers    = 4 // default for Config.MaxViewers
	writeDeadline = 5 * time.Second
	queuedActions = 64 // actions a control connection may have waiting
)

// A control client is pinged every pingEvery and dropped after silentFor without an answer.
var pingEvery, silentFor = 2 * time.Second, 6 * time.Second

var upgrader = websocket.Upgrader{
	ReadBufferSize:  1024,
	WriteBufferSize: 64 << 10,
	// guard() has checked the Origin against the Host already
	CheckOrigin: func(*http.Request) bool { return true },
}

type controlConn struct {
	ws   *websocket.Conn
	out  chan outMsg
	done chan struct{}
	once sync.Once
}

type outMsg struct {
	kind int
	data []byte
}

func (c *controlConn) send(kind int, data []byte) {
	select {
	case c.out <- outMsg{kind, data}:
	case <-c.done:
	default: // the client is not reading: stats and errors can be skipped
	}
}

func (c *controlConn) sendJSON(v any) {
	b, _ := json.Marshal(v)
	c.send(websocket.TextMessage, b)
}

func (c *controlConn) close(code int, reason string) {
	c.once.Do(func() {
		_ = c.ws.WriteControl(websocket.CloseMessage, websocket.FormatCloseMessage(code, reason),
			time.Now().Add(time.Second))
		close(c.done)
		_ = c.ws.Close()
	})
}

func (s *Server) control(w http.ResponseWriter, r *http.Request) {
	takeover := r.URL.Query().Get("takeover") == "true"
	ws, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		return
	}
	ws.SetReadLimit(64 << 10)
	c := &controlConn{ws: ws, out: make(chan outMsg, 64), done: make(chan struct{})}
	s.mu.Lock()
	prev := s.controller
	if prev != nil && !takeover {
		s.mu.Unlock()
		c.close(closeInUse, "another client controls this phone")
		return
	}
	s.controller = c
	s.mu.Unlock()
	if prev != nil {
		prev.close(closeInUse, "taken over by another client")
	}
	defer func() {
		s.mu.Lock()
		if s.controller == c {
			s.controller = nil
		}
		s.mu.Unlock()
		s.engine.ReleaseAll()
		c.close(websocket.CloseNormalClosure, "")
	}()

	go c.writer()
	events, stop := s.engine.Subscribe()
	defer stop()
	go func() {
		tick := time.NewTicker(time.Second)
		defer tick.Stop()
		var lastErr time.Time
		for {
			select {
			case <-c.done:
				return
			case ev, ok := <-events:
				if !ok {
					return
				}
				if time.Since(lastErr) < 500*time.Millisecond {
					continue // a locked phone fails every report: tell the operator twice a second at most
				}
				lastErr = time.Now()
				c.sendJSON(map[string]any{"t": ev.Kind, "error": ev.Error, "count": ev.Count})
			case <-tick.C:
				c.sendJSON(map[string]any{"t": "stats", "input": s.engine.Stats(), "usb": s.engine.Sink().Link()})
			}
		}
	}()
	c.sendJSON(map[string]any{"t": "hello", "device": s.cfg.DeviceID, "version": s.cfg.Version})

	// the connection's actions run in order on one worker; they end when the connection does
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	type job struct {
		id   json.RawMessage
		name string
		p    Params
	}
	jobs := make(chan job, queuedActions)
	go func() {
		for {
			select {
			case <-ctx.Done():
				return
			case j := <-jobs:
				actx, acancel := context.WithTimeout(ctx, Timeout(j.name, j.p))
				res, err := s.Run(actx, j.name, j.p)
				acancel()
				c.sendJSON(resultMessage(j.id, j.name, res, err))
			}
		}
	}()

	// liveness: a client that went away without closing (a sleeping laptop, a dropped network)
	// must not keep the phone and its held buttons
	alive := func() { _ = ws.SetReadDeadline(time.Now().Add(s.silentFor)) }
	alive()
	ws.SetPongHandler(func(string) error { alive(); return nil })
	go func() {
		tick := time.NewTicker(s.pingEvery)
		defer tick.Stop()
		for {
			select {
			case <-c.done:
				return
			case <-tick.C:
				if ws.WriteControl(websocket.PingMessage, nil, time.Now().Add(writeDeadline)) != nil {
					return
				}
			}
		}
	}()

	for {
		kind, data, err := ws.ReadMessage()
		if err != nil {
			return
		}
		alive()
		if kind == websocket.BinaryMessage {
			s.liveMessage(c, data)
			continue
		}
		var raw map[string]json.RawMessage
		if json.Unmarshal(data, &raw) != nil || string(raw["t"]) != `"action"` {
			c.sendJSON(map[string]any{"t": "error", "code": "bad_request", "error": "unknown message: send {\"t\": \"action\", \"action\": ...}"})
			continue
		}
		id := raw["id"]
		var name string
		_ = json.Unmarshal(raw["action"], &name)
		delete(raw, "t")
		delete(raw, "id")
		delete(raw, "action")
		p, err := ParseParams(name, raw)
		if err != nil {
			c.sendJSON(resultMessage(id, name, ActionResult{}, err))
			continue
		}
		select {
		case jobs <- job{id, name, p}:
		default:
			c.sendJSON(map[string]any{"t": "result", "id": id, "action": name, "ok": false, "code": "busy",
				"error": fmt.Sprintf("%d actions are waiting already", queuedActions)})
		}
	}
}

// resultMessage is the answer to an action sent over the control socket.
func resultMessage(id json.RawMessage, name string, res ActionResult, err error) map[string]any {
	reply := map[string]any{"t": "result", "id": id, "action": name, "ok": err == nil}
	if res.Action != "" {
		reply["result"] = res
	}
	if err != nil {
		_, code := errorCode(err)
		reply["code"], reply["error"] = code, err.Error()
	}
	return reply
}

func (s *Server) liveMessage(c *controlConn, b []byte) {
	if len(b) == 0 {
		return
	}
	switch b[0] {
	case msgTouch:
		if len(b) < 7 {
			return
		}
		s.engine.Live(input.Touch{
			Buttons: b[1],
			X:       float64(binary.LittleEndian.Uint16(b[2:])) / 65535,
			Y:       float64(binary.LittleEndian.Uint16(b[4:])) / 65535,
			Wheel:   int(int8(b[6])),
		})
	case msgKeys:
		if len(b) < 3 || len(b) < 3+int(b[2]) || b[2] > 6 {
			return
		}
		s.engine.LiveKeys(hid.KeyState{Mods: b[1], Keys: append([]uint8(nil), b[3:3+int(b[2])]...)})
	case msgConsumer:
		if len(b) < 4 {
			return
		}
		s.engine.LiveConsumer(uint32(b[1]) | uint32(b[2])<<8 | uint32(b[3])<<16)
	case msgRelease:
		s.engine.ReleaseAll()
	case msgPing:
		if len(b) >= 5 {
			c.send(websocket.BinaryMessage, append([]byte(nil), b[:5]...))
		}
	}
}

func (c *controlConn) writer() {
	for {
		select {
		case <-c.done:
			return
		case m := <-c.out:
			_ = c.ws.SetWriteDeadline(time.Now().Add(writeDeadline))
			if err := c.ws.WriteMessage(m.kind, m.data); err != nil {
				c.close(websocket.CloseAbnormalClosure, "")
				return
			}
		}
	}
}

// -- the screen stream ---------------------------------------------------------------------------------------

// Stream protocol: binary messages are frames, a 12-byte header then the JPEG:
//
//	'F' 1 0 0  seq:u32  age_us:u32   (age: from capture to sending)
//
// With ack=true (the default) the box sends the next frame only once the client answered
// "ack <frame number>" (or "ack") for the previous one, or after a second without an answer, so a
// slow browser skips frames instead of falling behind. Text messages are {"t": "status", "status":
// {...}}, at connection and every second.
func (s *Server) stream(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	quality := clampQuery(q.Get("quality"), 75, 30, 95)
	fps := clampQuery(q.Get("fps"), 60, 1, 60)
	ack, err := queryBool(q, "ack", true)
	if err != nil {
		writeError(w, http.StatusBadRequest, "bad_request", err.Error())
		return
	}
	release, ok := s.viewer()
	if !ok {
		ws, err := upgrader.Upgrade(w, r, nil)
		if err == nil {
			_ = ws.WriteControl(websocket.CloseMessage, websocket.FormatCloseMessage(closeTooMany,
				fmt.Sprintf("this box already streams to its most viewers (%d)", s.maxViewers())), time.Now().Add(time.Second))
			_ = ws.Close()
		}
		return
	}
	defer release()
	ws, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		return
	}
	defer ws.Close()
	ws.SetReadLimit(4096)
	ctx, cancel := context.WithCancel(r.Context())
	defer cancel()
	// acks carry the frame number they answer ("ack 42"); a bare "ack" answers the frame in flight
	acks := make(chan uint64, 4)
	go func() {
		defer cancel()
		for {
			_, data, err := ws.ReadMessage()
			if err != nil {
				return
			}
			var n uint64
			switch msg := string(data); {
			case msg == "ack" || msg == `{"t":"ack"}`:
			case strings.HasPrefix(msg, "ack "):
				if n, err = strconv.ParseUint(msg[4:], 10, 32); err != nil {
					continue
				}
			default:
				continue
			}
			select {
			case acks <- n:
			default:
			}
		}
	}()
	var wmu sync.Mutex
	write := func(kind int, data []byte) error {
		wmu.Lock()
		defer wmu.Unlock()
		_ = ws.SetWriteDeadline(time.Now().Add(writeDeadline))
		return ws.WriteMessage(kind, data)
	}
	status := func() error {
		b, _ := json.Marshal(map[string]any{"t": "status", "status": s.Snapshot()})
		return write(websocket.TextMessage, b)
	}
	if status() != nil {
		return
	}
	go func() {
		tick := time.NewTicker(time.Second)
		defer tick.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-tick.C:
				if status() != nil {
					cancel()
					return
				}
			}
		}
	}()
	var seq uint64
	minGap := time.Second / time.Duration(fps)
	var last time.Time
	for {
		f, err := s.hub.Next(ctx, seq)
		if err != nil {
			return
		}
		if gap := time.Since(last); gap < minGap {
			select {
			case <-time.After(minGap - gap):
			case <-ctx.Done():
				return
			}
			if nf := s.hub.Latest(); nf != nil && nf.Seq > f.Seq {
				f = nf
			}
		}
		seq = f.Seq
		jpeg, err := s.hub.JPEG(f, quality)
		if err != nil {
			continue
		}
		msg := make([]byte, 12+len(jpeg))
		msg[0], msg[1] = 'F', 1
		binary.LittleEndian.PutUint32(msg[4:], uint32(f.Seq))
		binary.LittleEndian.PutUint32(msg[8:], uint32(time.Since(f.At).Microseconds()))
		copy(msg[12:], jpeg)
		last = time.Now()
		if write(websocket.BinaryMessage, msg) != nil {
			return
		}
		if ack {
			timeout := time.After(time.Second) // the client stopped answering: do not stall forever
		wait:
			for {
				select {
				case n := <-acks:
					if n == 0 || n == uint64(uint32(f.Seq)) {
						break wait
					} // a late answer to an earlier frame: keep waiting for this one
				case <-timeout:
					break wait
				case <-ctx.Done():
					return
				}
			}
		}
	}
}

func clampQuery(v string, def, lo, hi int) int {
	n, err := strconv.Atoi(v)
	if err != nil {
		return def
	}
	if n < lo {
		return lo
	}
	if n > hi {
		return hi
	}
	return n
}
