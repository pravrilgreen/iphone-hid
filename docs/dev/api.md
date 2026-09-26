# API

Every box serves one phone. The machine-readable description is at `/api/openapi.json` on the box.

- **Base URL:** `http://<box>:8000`. Boxes announce themselves over mDNS as `_ihc._tcp` with the TXT
  records `api=/api`, `version`, `auth` and `id`.
- **Token:** every call except `/api/health` needs the box's token: `Authorization: Bearer <token>`,
  or `?token=` where headers cannot be set (WebSockets, image URLs). The token is in
  `/var/lib/ihc/token` on the box.
- **Browsers:** requests from pages of another origin are refused, and so are host names the box does
  not answer to (IP addresses, `localhost`, `*.local`, its host name, and `--allow-host` names are
  accepted).
- **Coordinates** are fractions of the phone screen: `(0, 0)` is the top left corner, `(1, 1)` the
  bottom right, whatever the phone model.

## Status

`GET /api/devices` lists the box's phone; `GET /api/devices/{id}` returns its status:

```json
{
  "id": "iphone-b40d9e",
  "state": "ready",
  "message": "",
  "usb": {"udc": "fc000000.usb", "state": "configured", "connected": true, "profile": "RA"},
  "video": {"state": "ok", "format": "NV12", "width": 1920, "height": 1080, "fps": 60, "screen": {"x": 710, "y": 0, "w": 498, "h": 1080}},
  "input": {"reports": 812, "rate": 406, "latency_p50_ms": 0.08, "latency_p95_ms": 0.31, "errors": 0},
  "screen": {"width": 498, "height": 1080, "landscape": false},
  "buttons": [{"name": "home", "label": "Home", "how": "keyboard shortcut Cmd+H"}],
  "last_action": {"action": "tap", "ok": true, "ms": 262.4},
  "live": false
}
```

`state` is `ready`, `busy` (a scripted action runs), `starting` (no picture yet since the box
started), `no_usb` (the phone has not taken the gadget:
unplugged, locked, or the accessory not allowed), `asleep` (the phone suspended the USB bus) or
`no_video`. `message` says what to do.

## Actions

`POST /api/devices/{id}/<action>` with a JSON body. Each call returns once the phone has taken the
action's last report: `{"ok": true, "result": {"action": "tap", "ok": true, "ms": 262.4}}`.

| Action | Body | What it does |
|---|---|---|
| `tap` | `x`, `y`, `hold_ms` (80) | Move there, wait for the pointer to settle, press, release |
| `long_press` | `x`, `y`, `duration_ms` (1500) | Touch and hold |
| `swipe` | `x1`, `y1`, `x2`, `y2`, `duration_ms` (250) | Lift while moving: lists keep the swipe's speed |
| `drag` | `x1`, `y1`, `x2`, `y2`, `hold_ms` (600), `duration_ms` (600), `rest_ms` (200) | Hold (lifts an icon), move, stay still, lift |
| `scroll` | `x`, `y`, `lines` | Wheel lines; positive scrolls towards the top |
| `type` | `text` | Printable ASCII, newline, tab; the U.S. keyboard layout |
| `key` | `combo` | E.g. `cmd+space`, `esc`, `cmd+shift+3` |
| `button` | `name` | `home`, `app_switcher`, `spotlight`, `volume_up`, `volume_down`, `mute`, `play_pause` |
| `home`, `app_switcher`, `spotlight`, `volume_up`, ... | none | The same buttons, one action each |
| `media` | `key` | Any consumer key: `volume_up`, `next_track`, ... |
| `open_url` | `url` | Search, type the URL, Return |
| `wake` | none | USB remote wakeup of a sleeping phone, then a key |
| `release_all` | none | Release every key and button |

Errors: `400 bad_request` (nothing was sent to the phone), `401 unauthorized`, `404 no_device`,
`503 no_usb` or `asleep`, `500 failed`. The body is `{"ok": false, "code": "...", "error": "..."}`.

`POST /api/devices/{id}/orientation` with `{"landscape": true}` takes the screen from a landscape
mirror.

## Screen

- `GET /api/devices/{id}/screenshot`: JPEG (`quality=`, default 90) or PNG (`format=png`); with
  `wait=true` a frame captured after the request. The header `X-Frame-Age-Ms` gives the frame's age.
- `GET /api/devices/{id}/mjpeg`: a multipart MJPEG stream (`fps=`, `quality=`, `frames=`).
- `GET /api/devices/{id}/stream` (WebSocket): binary messages are frames, a 12-byte header then the
  JPEG:

  | Bytes | Content |
  |---|---|
  | 0–3 | `'F'`, version 1, 0, 0 |
  | 4–7 | frame number, u32 little endian |
  | 8–11 | microseconds from capture to sending, u32 little endian |

  Answer the text message `ack` after drawing each frame: the box sends the next frame only then
  (`ack=false` turns this off). Text messages are the status JSON, at connection and every second.
  Query: `quality` (75), `fps` (60).

## Live control

`GET /api/devices/{id}/control` (WebSocket). One client controls a phone at a time; another one is
refused with close code 4409 unless it connects with `?takeover=true`, which closes the first with
4409. Binary messages, little endian:

| Message | Bytes | Meaning |
|---|---|---|
| touch | `0x01` buttons x:u16 y:u16 wheel:i8 | Pointer position (0 to 65535 over the screen), buttons held (1 touch, 2 secondary, 4 middle), wheel lines |
| keys | `0x02` mods n keys[n] | Keyboard modifiers and up to 6 keys held (HID usages) |
| consumer | `0x03` bits:u24 | Consumer keys held (bit 0 volume up, 1 volume down, 2 mute, 3 play/pause, ...) |
| release | `0x04` | Release everything |
| ping | `0x05` seq:u32 | Echoed back at once, for the round-trip time |

Send a touch message for every pointer event: the box keeps only the newest position while a report
waits for the phone, and never merges a press or a release. A press that follows a jump waits
until iOS has glided the pointer there (80 ms, `--settle`). Input older than 0.5 s when its turn
comes (a scripted action held the phone) is dropped, except releases.

Text messages run actions in order with the live input:
`{"t": "action", "id": 1, "action": "tap", "x": 0.5, "y": 0.5}` is answered by
`{"t": "result", "id": 1, "ok": true, "result": {...}}`. The box sends `{"t": "hello"}`,
`{"t": "stats", "input": {...}}` every second, and `{"t": "error"}` or `{"t": "dropped"}` when input
could not reach the phone.

## Python SDK

```python
from ihc import Farm, IhcError

with Farm("http://box-a.local:8000", "http://box-b.local:8000", token="...") as farm:
    for phone in farm.devices():
        print(phone.id, phone.state)
    phone = farm.device("iphone-b40d9e")
    phone.tap(0.5, 0.5)
    phone.long_press(0.2, 0.3)
    phone.swipe(0.5, 0.8, 0.5, 0.2, duration_ms=300)
    phone.drag(0.2, 0.3, 0.7, 0.3)
    phone.scroll(0.5, 0.5, -3)
    phone.type("hello")
    phone.key("cmd+space")
    phone.button("volume_up")
    phone.home(); phone.app_switcher(); phone.spotlight(); phone.wake()
    png = phone.screenshot("screen.png")
```

`Farm.discover()` finds the boxes on the local network (the `discovery` extra:
`pip install "iphone-hid[discovery] @ git+https://github.com/pravrilgreen/iphone-hid"`), with the scheme
each box announces (`http` or `https`).
The token defaults to `$IHC_TOKEN`. Failed calls raise `IhcError` with `status_code` and `code`.

The `ihc` command does the same from a shell: `ihc devices`, `ihc tap 0.5 0.5`,
`ihc swipe 0.5 0.8 0.5 0.2`, `ihc type hello`, `ihc button home`, `ihc screenshot shot.png`
(`--url`, `--token`, `--device`).
