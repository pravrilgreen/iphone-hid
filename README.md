# iphone-hid

**Remote touch, keyboard and screen for real iPhones.** One small box per phone. People use it from
a browser; test frameworks use its HTTP API or the Python SDK.

![The console: the phone's live screen in a device frame, its buttons, and the typing and media panels](docs/images/console.png)

## What it does

- **Live screen** in the browser, cut to the phone, at the phone's frame rate.
- **Touch as it happens.** Click to tap, drag to swipe or move things, scroll with the wheel, hold for
  a long press. The box drives the iPhone's absolute pointer, so every touch lands where it was made.
- **Keyboard.** Type straight onto the phone, or paste text.
- **Phone buttons.** Home, App Switcher, Search, volume, mute, play/pause, and wake.
- **Automation.** A REST API with an OpenAPI description, a WebSocket for live control, a Python SDK
  and the `ihc` command. Boxes announce themselves on the local network.
- **One file to install.** A 3.4 MB self-installing bundle for ARM64 boards.

## How it works

![The test framework and the operator reach the box over the network. The box is the iPhone's touch pointer and keyboard over USB, and reads the iPhone's screen from its HDMI input. A USB-C hub joins both to the iPhone with one cable.](docs/images/diagram-overview.png)

- **Touch and keys.** The board's USB-C port runs as a USB device: an absolute pointer whose range
  covers the whole screen, a keyboard, and media keys. iOS shows the pointer through AssistiveTouch.
  The box sends each report as soon as the input arrives. While a report waits for the iPhone to
  collect it, newer moves replace the waiting one, so the pointer follows the hand without a queue;
  presses and releases always keep their order.
- **Screen.** The iPhone mirrors its screen over HDMI into the board's HDMI input. The box cuts the
  phone out of each 1080p frame, encodes it as JPEG with libjpeg-turbo, and sends it to each viewer
  as soon as the viewer has drawn the previous one.
- **Software.** `ihcd`, one static Go binary: the API, the console, the USB gadget set-up and the
  video capture.

## Performance

| Path | Measured |
|---|---|
| Touch on the box: from the input arriving to its report queued on the USB port | under 0.1 ms typical, 0.3 ms at worst |
| JPEG encoding of the phone screen (498 × 1080) | 3.0 ms per frame |
| Frame rate | the iPhone's mirror rate, up to 60 fps |

Measured with the simulated iPhone on a development machine. On each box, the console's top bar
shows the touch latency, the picture rate and age, and the network round trip, live.

## What each box needs

| Part | Notes |
|---|---|
| Orange Pi 5 Plus and its 5 V / 4 A supply | Runs the box: USB device port, HDMI input, network |
| USB-C hub with HDMI, USB-A and a USB-C PD charging input | HDMI through DisplayPort Alt Mode, e.g. UGREEN Revodok 105 (15495) |
| USB-C charger, 30 W or more | Into the hub's PD input: powers the hub, charges the phone |
| HDMI cable | Hub to the board's HDMI IN |
| USB-A to USB-C cable (data) | Hub's USB-A to the board's Type-C USB 3 port |
| Ethernet | API and console |

Phones: iPhone 15 and later with USB-C, except iPhone 16e and 17e, which have no video output.

![Wiring: the iPhone connects to the hub with one cable; the hub's HDMI goes to the board's HDMI IN, its USB-A port to the board's Type-C USB 3 port](docs/images/diagram-wiring-box.png)

## Install

1. Download `ihc-box-<version>-linux-arm64.run` from the
   [latest release](https://github.com/pravrilgreen/iphone-hid/releases/latest) and copy it to the board.
2. `sudo sh ihc-box-*-linux-arm64.run`. It installs `ihcd`, the USB gadget and the service, and
   starts them. The API token is in `/var/lib/ihc/token`.
3. Open `http://<board address>:8000`.

`ihcd check` shows what the board offers: its USB device port, the gadget, and the HDMI input.
The [install guide](docs/guide/install.md) covers the board image, the HDMI input and
troubleshooting.

## Set up the iPhone

Once per phone: AssistiveTouch on, the pointer never hidden, Auto-Lock set to Never, the wired
accessory allowed, the U.S. hardware keyboard layout, and the middle pointer button mapped to App
Switcher. Step by step: [iPhone setup](docs/guide/iphone-setup.md). The
[hardware check](docs/guide/hardware-check.md) tries every button on the real phone.

## Use it

**In the browser.** Hover moves the phone's pointer; click taps; drag swipes, scrolls lists or moves
icons; the wheel scrolls. Click the screen, then type: the keyboard goes to the phone, and
Cmd/Ctrl+V types the clipboard. The volume keys and the side button on the drawn phone press the
real ones; Home, App Switcher and Search sit under it.

**From code.**

```python
from ihc import Farm

phone = Farm.discover().devices()[0]        # or Farm("http://box.local:8000", token="...")
phone.tap(0.5, 0.93)                         # fractions of the screen: (0, 0) top left
phone.swipe(0.5, 0.8, 0.5, 0.2)
phone.drag(0.2, 0.3, 0.7, 0.3)
phone.type("hello\n")
phone.home()
phone.screenshot("screen.png")
```

```sh
curl -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
     -d '{"x": 0.5, "y": 0.93}' http://box.local:8000/api/devices/iphone-b40d9e/tap
```

`pip install "iphone-hid[discovery]"` installs the SDK and the `ihc` command. The API is described
at `/api/openapi.json` on every box and in the [API reference](docs/dev/api.md).

## Roadmap

| Stage | Status |
|---|---|
| Orange Pi 5 Plus box: touch, keyboard, buttons, screen, API, console | Software done; confirmed on an iPhone 15: the absolute pointer |
| Purpose-built box: the iPhone's USB-C straight into an LT7911D bridge, a CH32V305 USB controller, an RV1106 SoC running `ihcd` | Design: [custom box](docs/research/custom-box.md) |

## Repository

| Path | What |
|---|---|
| `box/` | `ihcd`, the box software (Go): touch engine, USB gadget, video, API, console (`box/web`), simulated iPhone |
| `src/ihc/` | Python SDK and the `ihc` command |
| `packaging/` | systemd units, udev rules, installer, bundle header |
| `scripts/` | bundle build and smoke test, diagrams |
| `docs/` | guides, API, architecture, research ([index](docs/README.md)) |

## Development

```sh
make dev          # Python SDK in .venv with the test tools
make sim          # the box with a simulated iPhone on http://localhost:8000
make test         # gofmt, go vet, the Go tests, the SDK tests against a simulated box
make box          # dist/ihc-box-<version>-linux-arm64.run (Go, cmake, zig 0.13)
make check-box    # smoke-test the bundle (under qemu-aarch64 elsewhere)
```

Changes are listed in the [changelog](CHANGELOG.md).
