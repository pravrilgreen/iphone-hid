# iphone-hid

Control an iPhone (view screen + tap/swipe/type) from a Linux host using only external hardware:
HDMI mirroring into a capture card for video, and a HID chip (CH9329 over serial, or an ESP32 BLE
bridge speaking the same protocol) for input. No Developer Mode, no jailbreak, no app on the phone.

See [docs/handoff.md](docs/handoff.md) for the full design, constraints and roadmap.

## Layout

```
ihc/hid       HID backends (CH9329 serial, fake device)
ihc/input     pointer model, keymap, gestures
ihc/vision    capture, screen rect, pointer detection
ihc/control   closed-loop pointer, calibration
ihc/api       HTTP/WebSocket server
web/          browser UI
tools/        hardware test CLIs
firmware/     ESP32 BLE HID bridge (ESP-IDF)
tests/
docs/         hardware setup, test logs
```

## Status

Phase 0 (hardware verification) — not started. The CH9329 frame codec is implemented and unit
tested, but the protocol details are still unverified against the WCH datasheet.

## Dev

```
pip install -e .[dev]
pytest
```
