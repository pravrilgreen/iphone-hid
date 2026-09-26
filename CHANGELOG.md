# Changelog

Each release publishes one file for ARM64 boards: `ihc-box-<version>-linux-arm64.run`. Versions 0.x
are pre-releases: the product has not been tested as a whole on real hardware yet, and the API may
still change.

## Unreleased

- **Box v1 hardware (`hardware/box-v1`):**
  - A draft 4-layer layout, 96×66 mm, generated from the netlist by `pcb.py`: every part placed, routed
    except the LT7911D area and 8 listed connections, no DRC errors.
  - Second design review: CH224K VBUS pin left open, a TVS that clamps below the buck's input rating, ESD on
    the rear CC lines, a gentler LT7911D reset, I2C pull-ups on the module's own 3.3 V, a hybrid iPhone
    receptacle that can be routed, smaller buttons.

## 0.1.0: first pre-release

Version numbers start here. Earlier builds were development versions and are not supported.

- **The box software, `ihcd`:** one static Go binary for the Orange Pi 5 Plus.
  - It makes the board's USB-C port the iPhone's absolute pointer, keyboard and media keys.
  - It reads the iPhone's screen from the board's HDMI input.
  - It serves the console and the API.
  - It installs from a 3.5 MB self-extracting bundle.
- **Touch as it happens:**
  - Every pointer event reaches the phone as soon as it can take it.
  - Presses and releases land where they were made.
  - Hover clicks go out at once.
  - Scripted gestures (tap, long press, swipe, drag, scroll, typing, keys, the phone's buttons) run
    in order with live input.
- **`ihcd doctor`:**
  - It examines the board and the box part by part, and says how to fix each problem.
  - `--fix` fixes what can be fixed at run time; `--fix-safe` leaves out the fixes that could cut
    something else off the USB port (the installer runs it).
  - `--fix-boot` also turns the HDMI input on in the boot configuration, after checking that the
    overlays still apply, keeping a backup.
- **The API:**
  - REST actions, JSON errors with codes, and a complete OpenAPI description.
  - A live control WebSocket (actions in order, one operator at a time) and a screen stream with
    per-frame acks.
  - Token authentication, host and origin checks, optional HTTPS, and mDNS announcement.
- **The console:**
  - The phone drawn with its hardware buttons, and icon controls.
  - Live latency, picture rate and round trip.
  - Typing and pasting.
  - A set-up and use guide with animated wiring drawings that shows the box's own state.
- **Python SDK and the `ihc` command:**
  - `Farm` and `Phone` over the API.
  - Retryable error codes.
  - `Farm.discover()` on the local network.
- **Simulated iPhone** (`ihcd serve --sim`, on Linux or macOS) with USB and HDMI faults, for
  development, the SDK tests and the demo videos (`make demo`).
- **The purpose-built box:** pin-level schematic, BOM with orderable parts, and an order package
  (`hardware/box-v1`). Its layout waits for the LT7911D's documents.

Verified on hardware so far: the gadget's absolute pointer on an iPhone 15. The rest is tested
against the simulated iPhone; the [hardware check](docs/guide/hardware-check.md) on a board is the
next step.
