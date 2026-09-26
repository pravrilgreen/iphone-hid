# Changelog

Each release publishes one file for ARM64 boards: `ihc-box-<version>-linux-arm64.run`.

## 0.3.0: 2026-09-26

The box software is rebuilt as `ihcd`, one static Go binary.

- **Touch in real time.** The console streams every pointer event to the box as a 7-byte message;
  the box sends each report as soon as the phone can take it, keeping only the newest position
  while one waits and never merging a press or a release. Drags, swipes and the wheel act while the
  hand moves. A press after a jump waits for iOS to glide the pointer there.
- **Absolute pointer only.** The relative pointer and the Safari calibration are gone.
- **Buttons.** Home (Cmd+H, or the secondary pointer button with `--home button`), App Switcher,
  Search, volume up and down, mute, play/pause, and wake, in the console and the API.
- **New console.** The phone drawn as a device with its hardware buttons, live touch latency,
  picture rate and round trip, typing and pasting, media keys, orientation, screenshots.
- **API.** Drag and drop (`drag`), `button`, `wake`, PNG screenshots, an OpenAPI description, a
  binary live-control protocol, a screen stream with per-frame acknowledgements.
- **Video.** libjpeg-turbo encodes the phone's screen in about 3 ms; video-range colour is expanded
  to full range.
- **Install.** A 3.4 MB bundle; the installer replaces an earlier version's services. `ihcd check`
  explains a missing USB device port or HDMI input.
- **Python** is the SDK and the `ihc` command, tested against the box software.

## 0.2.0 and earlier

The box software in Python, with the relative pointer, Safari calibration and a CH9329 backend.
