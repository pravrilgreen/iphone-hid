# Changelog

Releases publish one self-contained file for the Orange Pi 5 Plus box: `ihc-box-<version>-linux-aarch64.run`.

## 0.2.0: 2026-09-25

**Repository reorganised for the product.**

- **Layout:**
  - the Python package lives in `src/ihc`, and the web console ships inside it;
  - `packaging/` holds the systemd units, udev rules, installers and bundle;
  - `scripts/` holds the build and diagram scripts;
  - the docs are grouped by reader: `docs/guide`, `docs/dev`, `docs/research`.
- **Commands:** the lab tools install as commands: `ihc-hidtest`, `ihc-capture-check`, `ihc-hid-loopback`.
  `tools/gadget.py` is gone: use `ihc gadget`.
- **Install extras:** they are now `server` (box server) and `dev`. The base install is enough for the SDK.
- **Version:**
  - `ihc --version` prints it;
  - it has one source (the bundle's VERSION file, `pyproject.toml`, or the installed metadata);
  - the server no longer reports 0.1.0 whatever the release.
- **CI:** lint and tests run on every push, on Python 3.10, 3.11 and 3.12.
- **Fix:** a WebSocket or MJPEG handler cancelled while it stopped its own tasks could raise one of their
  cancellations instead of its own, which the caller's cancel scope then let through (seen on Python 3.12).
- **Language plan:** [ADR 0001](docs/dev/adr/0001-languages.md) (Go daemon and C on the purpose-built
  box; Python for the SDK, the reference server and the tools).
- **Releases:** older releases were removed; their changes are listed below.

## 0.1.9

- A sleeping iPhone suspends the USB bus and takes no input.
- The gadget declares USB remote wakeup. `ihc gadget wake` and hidtest `wake` wake a phone that
  suspended the bus.
- Setup guidance: Auto-Lock Never, Wired Accessories, passcode.
- Checklist test B9.

## 0.1.8

- `ihc-capture-check list` reads the board's boot script and says how to turn the HDMI input overlay on:
  in `overlays=`, through `user_overlays` (Armbian boot scripts older than 24.11), or with a reboot.

## 0.1.7

- The absolute pointer works without calibration: the console's Pointer switch, `ihc serve --pointer`,
  `IHC_POINTER`, `POST /api/devices/{id}/pointer`.

## 0.1.6

- Armbian: turn the HDMI input on with its `rk3588-hdmirx` overlay.
- `ihc-capture-check` waits up to 15 s for the first frame.

## 0.1.5

- Gadget profile `AR` (absolute pointer before the relative mouse).
- `ihc-capture-check list` explains a missing HDMI input.

## 0.1.4

- `ihc-capture-check list` reads the HDMI signal without v4l-utils.
- Video section of the quick test.

## 0.1.3

- `ihc-hidtest --gadget` is a flag again.
- The bundle unpacks quietly on boards whose clock is behind.

## 0.1.2

- Every gadget profile fits the kernel's limit of four HID functions.

## 0.1.1

- Offline bundle: a built-in V4L2 HDMI reader and EDID, no apt.

## 0.1.0

- First self-contained bundle for the Orange Pi 5 Plus: its own Python and libraries, the gadget service,
  the server.
