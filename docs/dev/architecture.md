# Architecture

The box runs `ihcd`, one static Go binary (`box/`). It has four parts.

## Touch engine (`box/internal/input`)

One goroutine sends every report, so presses, releases and key changes reach the phone in the
order they were made.

- **Live input** comes from the console over the control WebSocket, one small binary message per
  pointer event. A move merges into the pending one when the buttons are the same, so while a report
  waits for the phone to take the previous one, only the newest position is kept. A change of
  buttons never merges: a press lands where it was made, and a quick press and release both go out.
- **Settle.** iOS glides the pointer to a new absolute position. A press that follows a jump of more
  than 1% of the screen (a touch screen, a script) waits until 80 ms after the jump; a mouse hovering
  over the live view keeps the pointer in place, so its clicks go out at once.
- **Scripted gestures** (tap, long press, swipe, drag, scroll, typing, key combinations, buttons)
  run in the same goroutine, in order with the live input. Each ends with everything released and
  waits until the phone has taken its last report.
- **Stale input.** Live input that waited more than 0.5 s behind a scripted action is dropped,
  except releases, so nothing is replayed onto a screen that changed meanwhile and nothing stays
  held.
- **Latency** from the input arriving to its report being queued on the USB port is measured for
  every report; the console shows the median and the 95th percentile of the last two seconds.

Timings: settle 80 ms, tap hold 80 ms, long press 1.5 s, three releases 15 ms apart after a gesture,
key hold 30 ms, 20 ms between typed characters, a swipe or drag point every 8 ms.

## USB gadget (`box/internal/hid`)

The kernel's HID function, set up through configfs: one USB interface per report type, without
report IDs, in the order keyboard, consumer control, relative mouse, absolute pointer (profile `RA`,
the layout an iPhone 15 follows). The absolute pointer has 3 buttons, X and Y from 0 to 32767 over
the whole screen, and a wheel. The configuration declares remote wakeup, so the box can wake a phone
that suspended the bus.

Each interface is a `/dev/hidgN` node. f_hid keeps one report in flight per node: a node polls
writable again once the host has taken the report. The writer waits for that before each write, up
to 250 ms, so a phone that stopped polling shows up as an error instead of a hang.

## Video (`box/internal/video`)

- **Capture.** The HDMI receiver's V4L2 node (the Orange Pi 5 Plus `rk_hdmirx`), multi-planar or
  single-planar, mapped buffers. At start the box writes an EDID for 1920x1080 at 60 Hz, so the
  phone mirrors at 1080p. The source restarts after signal loss with a growing pause.
- **Screen area.** The phone's mirror fills the frame's height in portrait (black bars at the sides)
  or its width in landscape; the area is the phone's aspect ratio centred in the frame.
- **Conversion.** NV12, NV16, NV24 (and their V-first variants), YUYV, UYVY, BGR3 and RGB3. The phone
  area is copied out of the driver's buffer, with video-range YCbCr (16 to 235) expanded to the full
  range JPEG expects.
- **JPEG.** libjpeg-turbo (NEON on ARM64) in the release build: 3 ms for 498 × 1080 on a
  development machine. Go's `image/jpeg` in development builds.
- **Hub.** The newest frame is kept; each viewer gets the newest frame after the one it has drawn,
  and each frame is encoded once per quality, by whoever asks first.

## Server (`box/internal/server`)

The REST actions, the status, screenshots, MJPEG, the stream and control WebSockets, the OpenAPI
description and the console (`box/web`, built into the binary). Token authentication, host and
origin checks, mDNS announcement. See [API](api.md).

## Simulated iPhone (`box/internal/sim`)

`ihcd serve --sim` runs a simulated phone behind the same interfaces: it takes the HID reports and
produces the HDMI frames of its screen (home screen pages, a scrolling list with momentum, Notes,
Search, an app switcher, a volume indicator). The console, the API and the Python SDK tests run
against it; `/api/devices/{id}/sim` reports what it shows.

## Python SDK (`src/ihc`)

`Farm`, `Phone` and the `ihc` command over the HTTP API; `Farm.discover()` browses mDNS. Its tests
run against `ihcd serve --sim`.
