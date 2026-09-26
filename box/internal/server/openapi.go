package server

import (
	"net/http"
	"sort"

	"github.com/pravrilgreen/iphone-hid/box/internal/input"
)

func frac(desc string) map[string]any {
	return map[string]any{"type": "number", "minimum": 0, "maximum": 1, "description": desc}
}

func millis(desc string, def int) map[string]any {
	return map[string]any{"type": "integer", "minimum": 0, "maximum": 10000, "default": def, "description": desc}
}

var actionSchemas = map[string]struct {
	summary  string
	props    map[string]any
	required []string
}{
	"tap": {"Tap a point", map[string]any{"x": frac("from the left edge"), "y": frac("from the top edge"),
		"hold_ms": millis("how long the finger stays down", 80)}, []string{"x", "y"}},
	"long_press": {"Touch and hold", map[string]any{"x": frac("from the left edge"), "y": frac("from the top edge"),
		"duration_ms": millis("how long to hold", 1500)}, []string{"x", "y"}},
	"swipe": {"Swipe: lift while moving, so lists keep their speed", map[string]any{"x1": frac("start"), "y1": frac("start"),
		"x2": frac("end"), "y2": frac("end"), "duration_ms": millis("start to end", 250)}, []string{"x1", "y1", "x2", "y2"}},
	"drag": {"Drag and drop: hold, move, rest, lift", map[string]any{"x1": frac("start"), "y1": frac("start"),
		"x2": frac("end"), "y2": frac("end"), "hold_ms": millis("hold before moving (lifts an icon)", 600),
		"duration_ms": millis("start to end", 600), "rest_ms": millis("still at the end before lifting", 200)},
		[]string{"x1", "y1", "x2", "y2"}},
	"scroll": {"Turn the scroll wheel", map[string]any{"x": frac("where"), "y": frac("where"),
		"lines": map[string]any{"type": "integer", "minimum": -100, "maximum": 100, "description": "positive scrolls towards the top"}},
		[]string{"x", "y", "lines"}},
	"type": {"Type text (printable ASCII, newline, tab; US keyboard layout on the phone)",
		map[string]any{"text": map[string]any{"type": "string", "maxLength": 4000}}, []string{"text"}},
	"key": {"Press a key combination", map[string]any{"combo": map[string]any{"type": "string", "examples": []string{"cmd+space", "esc", "cmd+shift+3"}}},
		[]string{"combo"}},
	"button": {"Press a phone button", map[string]any{"name": map[string]any{"type": "string", "enum": buttonNames()}}, []string{"name"}},
	"media":  {"Press a media key", map[string]any{"key": map[string]any{"type": "string", "examples": []string{"volume_up", "volume_down", "mute", "play_pause"}}}, []string{"key"}},
	"open_url": {"Open a URL through Search", map[string]any{"url": map[string]any{"type": "string"}}, []string{"url"}},
	"release_all": {"Release every key and button", map[string]any{}, nil},
	"wake":        {"Wake a sleeping phone (USB remote wakeup, then a key)", map[string]any{}, nil},
}

func buttonNames() []string { return buttonList() }

func (s *Server) openapi(w http.ResponseWriter, _ *http.Request) {
	errResp := map[string]any{"description": "error", "content": map[string]any{"application/json": map[string]any{
		"schema": map[string]any{"type": "object", "properties": map[string]any{
			"ok": map[string]any{"type": "boolean"}, "code": map[string]any{"type": "string"},
			"error": map[string]any{"type": "string"}}}}}}
	id := map[string]any{"name": "id", "in": "path", "required": true, "schema": map[string]any{"type": "string"}}
	paths := map[string]any{
		"/api/health": map[string]any{"get": map[string]any{"summary": "Box health (no token needed)", "security": []any{},
			"responses": map[string]any{"200": map[string]any{"description": "ok"}}}},
		"/api/devices": map[string]any{"get": map[string]any{"summary": "The phone of this box, with its status",
			"responses": map[string]any{"200": map[string]any{"description": "a list with one device"}}}},
		"/api/devices/{id}": map[string]any{"get": map[string]any{"summary": "Status: ready, busy, no_usb, asleep or no_video",
			"parameters": []any{id}, "responses": map[string]any{"200": map[string]any{"description": "status"}, "404": errResp}}},
		"/api/devices/{id}/screenshot": map[string]any{"get": map[string]any{"summary": "The phone screen as JPEG (or PNG with format=png)",
			"parameters": []any{id,
				map[string]any{"name": "format", "in": "query", "schema": map[string]any{"type": "string", "enum": []string{"jpeg", "png"}}},
				map[string]any{"name": "quality", "in": "query", "schema": map[string]any{"type": "integer", "default": 90}},
				map[string]any{"name": "wait", "in": "query", "schema": map[string]any{"type": "boolean"},
					"description": "wait for a frame captured after the request"}},
			"responses": map[string]any{"200": map[string]any{"description": "image", "content": map[string]any{
				"image/jpeg": map[string]any{}, "image/png": map[string]any{}}}, "503": errResp}}},
		"/api/devices/{id}/mjpeg": map[string]any{"get": map[string]any{"summary": "The screen as a multipart MJPEG stream",
			"parameters": []any{id}, "responses": map[string]any{"200": map[string]any{"description": "multipart/x-mixed-replace"}}}},
		"/api/devices/{id}/stream": map[string]any{"get": map[string]any{
			"summary": "WebSocket: JPEG frames (12-byte header: 'F' 1 0 0, seq u32, age_us u32), status JSON every second; answer \"ack\" per frame",
			"parameters": []any{id}, "responses": map[string]any{"101": map[string]any{"description": "switching protocols"}}}},
		"/api/devices/{id}/control": map[string]any{"get": map[string]any{
			"summary": "WebSocket: live touch, keys and actions (see the binary protocol in the documentation)",
			"parameters": []any{id, map[string]any{"name": "takeover", "in": "query", "schema": map[string]any{"type": "boolean"}}},
			"responses": map[string]any{"101": map[string]any{"description": "switching protocols"}}}},
		"/api/devices/{id}/orientation": map[string]any{"post": map[string]any{"summary": "Whether the phone mirrors in landscape",
			"parameters": []any{id}, "requestBody": map[string]any{"content": map[string]any{"application/json": map[string]any{
				"schema": map[string]any{"type": "object", "properties": map[string]any{"landscape": map[string]any{"type": "boolean"}},
					"required": []string{"landscape"}}}}},
			"responses": map[string]any{"200": map[string]any{"description": "ok"}}}},
	}
	names := make([]string, 0, len(actionSchemas))
	for n := range actionSchemas {
		names = append(names, n)
	}
	sort.Strings(names)
	for _, n := range names {
		a := actionSchemas[n]
		schema := map[string]any{"type": "object", "properties": a.props, "additionalProperties": false}
		if a.required != nil {
			schema["required"] = a.required
		}
		paths["/api/devices/{id}/"+n] = map[string]any{"post": map[string]any{
			"summary": a.summary, "parameters": []any{id}, "tags": []string{"actions"},
			"requestBody": map[string]any{"content": map[string]any{"application/json": map[string]any{"schema": schema}}},
			"responses": map[string]any{"200": map[string]any{"description": "done: {ok, result: {action, ok, ms}}"},
				"400": errResp, "503": errResp}}}
	}
	for _, b := range buttonList() {
		if _, ok := actionSchemas[b]; !ok {
			paths["/api/devices/{id}/"+b] = map[string]any{"post": map[string]any{"summary": "Press " + b,
				"parameters": []any{id}, "tags": []string{"buttons"},
				"responses": map[string]any{"200": map[string]any{"description": "done"}, "503": errResp}}}
		}
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"openapi": "3.1.0",
		"info": map[string]any{"title": "iphone-hid box", "version": s.cfg.Version,
			"description": "Touch, keyboard and screen of one iPhone. Coordinates are fractions of the phone screen: (0, 0) top left, (1, 1) bottom right."},
		"components": map[string]any{"securitySchemes": map[string]any{"token": map[string]any{"type": "http", "scheme": "bearer"}}},
		"security":   []any{map[string]any{"token": []any{}}},
		"paths":      paths,
	})
}

func buttonList() []string {
	var out []string
	for _, b := range input.Buttons {
		out = append(out, b.Name)
	}
	return out
}
