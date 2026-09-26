package server

import (
	"net/http"
	"sort"
	"strings"

	"github.com/pravrilgreen/iphone-hid/box/internal/input"
)

func frac(desc string) map[string]any {
	return map[string]any{"type": "number", "minimum": 0, "maximum": 1, "description": desc}
}

func millis(desc string, def int) map[string]any {
	return map[string]any{"type": "integer", "minimum": 0, "maximum": 10000, "default": def,
		"description": desc + " (0 or absent: the default)"}
}

func ref(name string) map[string]any { return map[string]any{"$ref": "#/components/schemas/" + name} }

func jsonBody(schema map[string]any, desc string) map[string]any {
	return map[string]any{"description": desc, "content": map[string]any{"application/json": map[string]any{"schema": schema}}}
}

func errorResponse(desc string) map[string]any { return jsonBody(ref("Error"), desc) }

func intQuery(name string, def, lo, hi int, desc string) map[string]any {
	return map[string]any{"name": name, "in": "query", "description": desc,
		"schema": map[string]any{"type": "integer", "default": def, "minimum": lo, "maximum": hi}}
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
		"lines":  map[string]any{"type": "integer", "minimum": -100, "maximum": 100, "description": "not 0; positive scrolls towards the top"},
		"amount": map[string]any{"type": "integer", "deprecated": true, "description": "older name of lines"}},
		[]string{"x", "y"}},
	"type": {"Type text (printable ASCII, newline, tab; the U.S. keyboard layout on the phone)",
		map[string]any{"text": map[string]any{"type": "string", "minLength": 1, "maxLength": 4000}}, []string{"text"}},
	"key": {"Press a key combination", map[string]any{"combo": map[string]any{"type": "string",
		"description": "modifiers (cmd, ctrl, alt, shift) and one key joined by +; the key names are in the API reference",
		"examples":    []string{"cmd+space", "esc", "cmd+shift+3"}}}, []string{"combo"}},
	"button": {"Press a phone button", map[string]any{"name": map[string]any{"type": "string", "enum": buttonList()}}, []string{"name"}},
	"media": {"Press a media key", map[string]any{"key": map[string]any{"type": "string",
		"examples": []string{"volume_up", "volume_down", "mute", "play_pause", "next_track"}}}, []string{"key"}},
	"open_url":    {"Open a URL through Search", map[string]any{"url": map[string]any{"type": "string"}}, []string{"url"}},
	"release_all": {"Release every key and button", map[string]any{}, nil},
	"wake":        {"Wake a sleeping phone (USB remote wakeup, then a key)", map[string]any{}, nil},
}

// operationID turns long_press into longPress.
func operationID(name string) string {
	parts := strings.Split(name, "_")
	for i := 1; i < len(parts); i++ {
		if parts[i] != "" {
			parts[i] = strings.ToUpper(parts[i][:1]) + parts[i][1:]
		}
	}
	return strings.Join(parts, "")
}

func schemas() map[string]any {
	obj := func(props map[string]any, required ...string) map[string]any {
		m := map[string]any{"type": "object", "properties": props}
		if len(required) > 0 {
			m["required"] = required
		}
		return m
	}
	str := map[string]any{"type": "string"}
	num := map[string]any{"type": "number"}
	integer := map[string]any{"type": "integer"}
	boolean := map[string]any{"type": "boolean"}
	return map[string]any{
		"Error": obj(map[string]any{"ok": map[string]any{"const": false}, "code": map[string]any{"type": "string",
			"enum": []string{"bad_request", "unauthorized", "bad_host", "bad_origin", "no_device", "unknown_action", "not_found",
				"method_not_allowed", "too_many", "no_usb", "asleep", "no_video", "timeout", "cancelled", "failed"}},
			"error": str, "result": ref("ActionResult")}, "ok", "code", "error"),
		"ActionResult": obj(map[string]any{"action": str, "ok": boolean, "error": str,
			"ms": map[string]any{"type": "number", "description": "from the start of the action to the phone taking its last report"},
			"at": map[string]any{"type": "string", "format": "date-time"}}, "action", "ok"),
		"ActionResponse": obj(map[string]any{"ok": map[string]any{"const": true}, "result": ref("ActionResult")}, "ok", "result"),
		"Status": obj(map[string]any{
			"id":      str,
			"version": str,
			"state": map[string]any{"type": "string", "enum": []string{"ready", "busy", "starting", "no_usb", "asleep", "no_video"},
				"description": "starting: no picture yet since the box started"},
			"message":     map[string]any{"type": "string", "description": "what to do when the state is not ready"},
			"usb":         obj(map[string]any{"udc": str, "state": str, "connected": boolean, "profile": str}),
			"video":       obj(map[string]any{"state": str, "error": str, "format": str, "width": integer, "height": integer, "fps": num}),
			"input":       obj(map[string]any{"reports": integer, "rate": num, "latency_p50_ms": num, "latency_p95_ms": num, "latency_max_ms": num, "errors": integer}),
			"screen":      obj(map[string]any{"width": integer, "height": integer, "landscape": boolean}),
			"buttons":     map[string]any{"type": "array", "items": obj(map[string]any{"name": str, "label": str, "how": str})},
			"last_action": map[string]any{"oneOf": []any{ref("ActionResult"), map[string]any{"type": "null"}}},
			"live":        map[string]any{"type": "boolean", "description": "an operator controls the phone live now"},
			"uptime_s":    num,
			"encoder":     str,
		}, "id", "state", "message"),
		"Health": obj(map[string]any{"ok": boolean, "version": str, "devices": integer, "auth": boolean}, "ok"),
	}
}

func (s *Server) openapi(w http.ResponseWriter, _ *http.Request) {
	id := map[string]any{"name": "id", "in": "path", "required": true, "schema": map[string]any{"type": "string"}}
	common := func(extra map[string]any) map[string]any {
		out := map[string]any{"401": errorResponse("no token or a wrong one"), "404": errorResponse("no such device")}
		for k, v := range extra {
			out[k] = v
		}
		return out
	}
	noVideo := errorResponse("no_video: no picture from the phone")
	tooMany := errorResponse("too_many: the box already streams to its most viewers (4 by default, --max-viewers)")
	paths := map[string]any{
		"/api/health": map[string]any{"get": map[string]any{"operationId": "health", "summary": "Box health (no token needed)",
			"security": []any{}, "responses": map[string]any{"200": jsonBody(ref("Health"), "the box answers")}}},
		"/api/devices": map[string]any{"get": map[string]any{"operationId": "listDevices", "summary": "The phone of this box, with its status",
			"responses": map[string]any{"200": jsonBody(map[string]any{"type": "object", "properties": map[string]any{
				"devices": map[string]any{"type": "array", "items": ref("Status")}}}, "the box's phone"),
				"401": errorResponse("no token or a wrong one")}}},
		"/api/devices/{id}": map[string]any{"get": map[string]any{"operationId": "getStatus",
			"summary":    "Status: ready, busy, starting, no_usb, asleep or no_video",
			"parameters": []any{id}, "responses": common(map[string]any{"200": jsonBody(ref("Status"), "the phone's status")})}},
		"/api/devices/{id}/screenshot": map[string]any{"get": map[string]any{"operationId": "screenshot",
			"summary": "The phone screen as JPEG, or PNG with format=png",
			"parameters": []any{id,
				map[string]any{"name": "format", "in": "query", "schema": map[string]any{"type": "string", "enum": []string{"jpeg", "jpg", "png"}, "default": "jpeg"}},
				intQuery("quality", 90, 30, 100, "JPEG quality"),
				map[string]any{"name": "wait", "in": "query", "schema": map[string]any{"type": "boolean"},
					"description": "wait (up to 3 s) for a frame captured after the request"}},
			"responses": common(map[string]any{
				"200": map[string]any{"description": "the image; X-Frame-Age-Ms gives its age",
					"content": map[string]any{"image/jpeg": map[string]any{}, "image/png": map[string]any{}}},
				"400": errorResponse("an unknown format, a bad quality or wait"), "503": noVideo})}},
		"/api/devices/{id}/mjpeg": map[string]any{"get": map[string]any{"operationId": "mjpeg", "summary": "The screen as a multipart MJPEG stream",
			"parameters": []any{id, intQuery("quality", 75, 30, 95, "JPEG quality"), intQuery("fps", 30, 1, 60, "frames a second at most"),
				intQuery("frames", 0, 0, 1<<30, "stop after this many frames (0: never)")},
			"responses": common(map[string]any{"200": map[string]any{"description": "multipart/x-mixed-replace; boundary=frame"},
				"429": tooMany, "503": noVideo})}},
		"/api/devices/{id}/stream": map[string]any{"get": map[string]any{"operationId": "stream",
			"summary": "WebSocket: JPEG frames (12-byte header: 'F' 1 0 0, frame number u32, age in µs u32) and " +
				"{\"t\": \"status\"} every second; answer \"ack <frame number>\" after drawing each frame",
			"parameters": []any{id, intQuery("quality", 75, 30, 95, "JPEG quality"), intQuery("fps", 60, 1, 60, "frames a second at most"),
				map[string]any{"name": "ack", "in": "query", "schema": map[string]any{"type": "boolean", "default": true},
					"description": "wait for the client's ack before the next frame"}},
			"responses": common(map[string]any{"101": map[string]any{"description": "switching protocols; closed with 4429 when the box already streams to its most viewers"}})}},
		"/api/devices/{id}/control": map[string]any{"get": map[string]any{"operationId": "control",
			"summary":    "WebSocket: live touch, keys and actions, one controller at a time (the binary protocol is in the API reference)",
			"parameters": []any{id, map[string]any{"name": "takeover", "in": "query", "schema": map[string]any{"type": "boolean"}}},
			"responses":  common(map[string]any{"101": map[string]any{"description": "switching protocols; closed with 4409 when another client controls the phone"}})}},
		"/api/devices/{id}/orientation": map[string]any{"post": map[string]any{"operationId": "setOrientation",
			"summary":    "Whether the phone mirrors in landscape",
			"parameters": []any{id}, "requestBody": map[string]any{"required": true, "content": map[string]any{"application/json": map[string]any{
				"schema": map[string]any{"type": "object", "properties": map[string]any{"landscape": map[string]any{"type": "boolean"}},
					"required": []string{"landscape"}, "additionalProperties": false}}}},
			"responses": common(map[string]any{"200": jsonBody(map[string]any{"type": "object", "properties": map[string]any{
				"ok": map[string]any{"type": "boolean"}, "landscape": map[string]any{"type": "boolean"}}}, "set"),
				"400": errorResponse("no landscape field")})}},
	}
	actionResponses := func() map[string]any {
		return common(map[string]any{
			"200": jsonBody(ref("ActionResponse"), "done: the phone has taken the action's last report"),
			"400": errorResponse("bad_request: nothing was sent to the phone"),
			"500": errorResponse("failed"),
			"503": errorResponse("no_usb or asleep: the phone is not taking input"),
			"504": errorResponse("timeout: the action did not finish in time"),
		})
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
			"operationId": operationID(n), "summary": a.summary, "parameters": []any{id}, "tags": []string{"actions"},
			"requestBody": map[string]any{"content": map[string]any{"application/json": map[string]any{"schema": schema}}},
			"responses":   actionResponses()}}
	}
	for _, b := range buttonList() {
		if _, ok := actionSchemas[b]; !ok {
			paths["/api/devices/{id}/"+b] = map[string]any{"post": map[string]any{"operationId": operationID(b),
				"summary": "Press " + strings.ReplaceAll(b, "_", " "), "parameters": []any{id}, "tags": []string{"buttons"},
				"responses": actionResponses()}}
		}
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"openapi": "3.1.0",
		"info": map[string]any{"title": "iphone-hid box", "version": s.cfg.Version,
			"description": "Touch, keyboard and screen of one iPhone. Coordinates are fractions of the phone screen: " +
				"(0, 0) top left, (1, 1) bottom right. Errors are {ok: false, code, error}."},
		"components": map[string]any{
			"securitySchemes": map[string]any{
				"token":      map[string]any{"type": "http", "scheme": "bearer"},
				"tokenQuery": map[string]any{"type": "apiKey", "in": "query", "name": "token", "description": "where headers cannot be set: WebSockets, image URLs"},
			},
			"schemas": schemas(),
		},
		"security": []any{map[string]any{"token": []any{}}, map[string]any{"tokenQuery": []any{}}},
		"paths":    paths,
	})
}

func buttonList() []string {
	var out []string
	for _, b := range input.Buttons {
		out = append(out, b.Name)
	}
	return out
}
