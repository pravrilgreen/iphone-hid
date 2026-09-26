# Architecture

The box runs `ihcd`, one static Go binary (`box/`). It has four parts, and a doctor that examines the
board.

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

## Doctor (`box/internal/doctor`, `box/internal/board`)

`ihcd doctor` runs one check per part, in the order the parts depend on each other: the board, the
kernel's gadget framework and HID function, the USB device port (device tree `dr_mode`, USB role
switches, the Type-C port's data role and whether anything is plugged in), other gadgets holding
the controller (configfs gadgets and legacy `g_*` modules), the box's gadget, its `/dev/hidg`
nodes, the link to the iPhone (the controller's state), the HDMI input (why it is missing: device
tree, driver, kernel config, boot overlays), its signal, the services, the API and the token. Each
result says what was found and either a fix doctor can make or advice for a person. Without root,
the checks that cannot read what they need say so and ask for `sudo`.

Fixes come in three kinds:

- **Run-time fixes** (`--fix`): mount configfs, load `libcomposite`, start `ihcd-gadget` or `ihcd`,
  node and token permissions. doctor then checks again.
- **Disruptive fixes**, also with `--fix`: switch a USB role switch to device mode, unbind another
  gadget, unload a legacy gadget module. Each may cut off something else that uses the port (a
  console over USB, ADB), so `--fix-safe` leaves them out; the installer runs `--fix-safe`.
- **Boot fixes** (`--fix-boot`): add the HDMI receiver's overlay to `/boot/armbianEnv.txt` (or
  `orangepiEnv.txt`), copying it to `/boot/overlay-user` when the boot script cannot load it by
  name. When the board has `fdtoverlay`, doctor first applies every overlay the boot script will
  load, the new one with them, to the base device tree: the boot script drops all overlays when one
  fails, so a failure refuses the change. The old file is kept as `<file>.ihc-<time>`, and the
  result prints the command that puts it back.

The checks read a root directory, so the tests run them on fake board trees.

## Simulated iPhone (`box/internal/sim`)

`ihcd serve --sim` runs a simulated phone behind the same interfaces: it takes the HID reports and
produces the HDMI frames of its screen (home screen pages, a scrolling list with momentum, Notes,
Search, an app switcher, a volume indicator). The console, the API and the Python SDK tests run
against it; `GET /api/devices/{id}/sim` reports what it shows. `POST /api/devices/{id}/sim` with
`{"usb": "unplugged"}` (`connected`, `asleep`) or `{"video": "no_signal"}` (`ok`) makes its cables
report a fault, so the console's and the clients' handling of each state can be tried. The gadget
and the HDMI input are Linux interfaces (`*_linux.go`); the simulated box also builds and runs on
macOS, so the console and the SDK can be worked on without a board.

## Python SDK (`src/ihc`)

`Farm`, `Phone` and the `ihc` command over the HTTP API; `Farm.discover()` browses mDNS. Its tests
run against `ihcd serve --sim`.
