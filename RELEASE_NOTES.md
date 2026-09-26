**iphone-hid 0.1.0: first pre-release.** Remote touch, keyboard and screen for an iPhone, from an
Orange Pi 5 Plus.

This is a pre-release. The box's absolute pointer is confirmed on an iPhone 15; the rest is tested
against the simulated iPhone and still has to go through the
[hardware check](https://github.com/pravrilgreen/iphone-hid/blob/main/docs/guide/hardware-check.md)
on a board.

**Install** (copy the file to the board, then):

    sudo sh ihc-box-*-linux-arm64.run

The installer ends with `ihcd doctor`, which checks every part of the board and the box and fixes
what it safely can. The console is at `http://<board address>:8000`; the API token is in
`/var/lib/ihc/token` (`sudo cat` it). The box is one static binary: it runs on the board's Ubuntu,
Debian or Armbian image and downloads nothing.

**What is in it:**
- Touch that lands where it was made, as the hand moves.
- The phone's buttons.
- Typing.
- The live screen.
- A console with a set-up guide.
- A REST and WebSocket API with an OpenAPI description.
- A Python SDK.
- `ihcd doctor` for the board.

**Guides:** [install](https://github.com/pravrilgreen/iphone-hid/blob/main/docs/guide/install.md),
[iPhone setup](https://github.com/pravrilgreen/iphone-hid/blob/main/docs/guide/iphone-setup.md),
[hardware check](https://github.com/pravrilgreen/iphone-hid/blob/main/docs/guide/hardware-check.md),
[API](https://github.com/pravrilgreen/iphone-hid/blob/main/docs/dev/api.md).

Apache License 2.0. Checksums are in `SHA256SUMS`.
