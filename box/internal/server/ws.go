package server

import (
	"context"
	"encoding/binary"
	"encoding/json"
	"net/http"
	"strconv"
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
// action and answers {"t": "result", "id": 1, "ok": true, "result": {...}}. The box sends
// {"t": "hello"}, {"t": "stats"} every second, and {"t": "error"} or {"t": "dropped"}.
const (
	msgTouch    = 0x01
	msgKeys     = 0x02
	msgConsumer = 0x03
	msgRelease  = 0x04
	msgPing     = 0x05
)

// Close codes.
const (
	closeInUse    = 4409
	closeTooMany  = 4429
	maxViewers    = 4
	writeDeadline = 5 * time.Second
)

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

	for {
		kind, data, err := ws.ReadMessage()
		if err != nil {
			return
		}
		if kind == websocket.BinaryMessage {
			s.liveMessage(c, data)
			continue
		}
		var msg struct {
			T      string          `json:"t"`
			ID     json.RawMessage `json:"id"`
			Action string          `json:"action"`
		}
		if json.Unmarshal(data, &msg) != nil || msg.T != "action" {
			c.sendJSON(map[string]any{"t": "error", "error": "unknown message"})
			continue
		}
		var p Params
		if err := json.Unmarshal(data, &struct {
			*Params
			T      string          `json:"t"`
			ID     json.RawMessage `json:"id"`
			Action string          `json:"action"`
		}{Params: &p}); err != nil {
			c.sendJSON(map[string]any{"t": "result", "id": msg.ID, "ok": false, "error": err.Error()})
			continue
		}
		go func(id json.RawMessage, name string, p Params) {
			ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
			defer cancel()
			res, err := s.Run(ctx, name, p)
			reply := map[string]any{"t": "result", "id": id, "action": name, "ok": err == nil, "result": res}
			if err != nil {
				reply["error"] = err.Error()
			}
			c.sendJSON(reply)
		}(msg.ID, msg.Action, p)
	}
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
// With ack=true (the default) the box sends the next frame only once the client answered "ack"
// for the previous one, so a slow browser skips frames instead of falling behind. Text messages
// are the status JSON, at connection and every second.
func (s *Server) stream(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	quality := clampQuery(q.Get("quality"), 75, 30, 95)
	fps := clampQuery(q.Get("fps"), 60, 1, 60)
	ack := q.Get("ack") != "false"
	s.mu.Lock()
	if s.viewers >= maxViewers {
		s.mu.Unlock()
		ws, err := upgrader.Upgrade(w, r, nil)
		if err == nil {
			_ = ws.WriteControl(websocket.CloseMessage, websocket.FormatCloseMessage(closeTooMany, "too many viewers"),
				time.Now().Add(time.Second))
			_ = ws.Close()
		}
		return
	}
	s.viewers++
	s.mu.Unlock()
	defer func() {
		s.mu.Lock()
		s.viewers--
		s.mu.Unlock()
	}()
	ws, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		return
	}
	defer ws.Close()
	ctx, cancel := context.WithCancel(r.Context())
	defer cancel()
	acks := make(chan struct{}, 1)
	go func() {
		defer cancel()
		for {
			_, data, err := ws.ReadMessage()
			if err != nil {
				return
			}
			if string(data) == "ack" || string(data) == `{"t":"ack"}` {
				select {
				case acks <- struct{}{}:
				default:
				}
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
			select {
			case <-acks:
			case <-time.After(time.Second): // the client stopped answering: do not stall forever
			case <-ctx.Done():
				return
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
