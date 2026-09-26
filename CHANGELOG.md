# Changelog

Each release publishes one file for ARM64 boards: `ihc-box-<version>-linux-arm64.run`.

## 0.4.0: 2026-09-26

- **`ihcd doctor`** examines the board and the box part by part (gadget support, USB device port,
  other gadgets, the gadget and its nodes, the iPhone's link, the HDMI input and its signal, the
  services, the API, the token) and says how to fix each problem. `--fix` fixes what can be fixed
  at run time, `--fix-boot` also turns the HDMI input on in the boot configuration (keeping a
  backup), `--json` for scripts. The installer runs it. `ihcd check` runs it too.
- **Touch.** A press or a release waiting behind a slow report is never merged into later moves, so
  it lands where it was made; hovering no longer delays clicks; a script starts with the
  operator's finger lifted; the first action after a start never sends the pointer to the corner.
- **Control socket.** Actions run in the order sent; a client that vanished loses the phone within
  6 s; results carry an error code.
- **API.** Each action takes only its own parameters; time limits follow the action's length (504
  `timeout`); every error is JSON with a code (`unknown_action`, `not_found`,
  `method_not_allowed`, ...); 8-bit PNG screenshots; no picture is a 503 at once; stream acks name
  their frame; a complete OpenAPI description (schemas, operation ids, errors).
- **HTTPS** with `--tls-cert` and `--tls-key`; mDNS announces the scheme.
- **Console.** The lock between operators holds (a displaced console cannot act on the phone), a
  box that went away clears its figures, plain words in the Connection panel, key names listed.
- **Reliability.** The USB sink follows a gadget that was set up again; the EDID is written again
  until it succeeds; a cancelled wait for a frame no longer leaks.
- **SDK and `ihc`.** Retryable error codes (`unreachable`, `timeout`, ...), `wait_ready()`,
  `media()`, volume and orientation methods; `$IHC_URL`; options after the command; exit codes.
- **Docs.** Status of what was verified on a real iPhone, limits, security, a farm's shared token,
  the board's preparation, the API's error codes; demo videos of every console state
  (`make demo`); research and the purpose-built box's design in English, with an order package
  (`hardware/box-v1/ORDERING.md`).

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
  (now `ihcd doctor`) explains a missing USB device port or HDMI input.
- **Python** is the SDK and the `ihc` command, tested against the box software.

## 0.2.0 and earlier

The box software in Python, with the relative pointer, Safari calibration and a CH9329 backend.
