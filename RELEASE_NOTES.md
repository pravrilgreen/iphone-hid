**iphone-hid box: remote touch, keyboard and screen for an iPhone, from an Orange Pi 5 Plus.**

**Install or update** (copy the file to the board, then):

    sudo sh ihc-box-*-linux-arm64.run

The installer ends with `ihcd doctor`, which checks every part of the board and the box and fixes
what it safely can. The console is at `http://<board address>:8000`; the API token is in
`/var/lib/ihc/token` (`sudo cat` it). The box is one static binary: it runs on the board's Ubuntu,
Debian or Armbian image and downloads nothing.

**New in this release**

- `ihcd doctor`: finds what stops a board from working (USB device port, other gadgets, HDMI input,
  services) and fixes it, or says exactly how. `--fix-boot` turns the HDMI input on in the boot
  configuration, keeping a backup.
- Touch that lands where it was made, even behind a slow report; no delay on hover clicks.
- A stricter, fully described API: actions in order on the control socket, JSON errors with codes,
  time limits that follow the action, a complete OpenAPI description, HTTPS.
- A console that keeps the one-operator rule and says plainly what is connected.
- SDK: retryable error codes, `wait_ready()`, media keys; `ihc` with `$IHC_URL` and exit codes.

**Status:** the absolute pointer is confirmed on an iPhone 15; this version is tested against the
simulated iPhone, and its run through the hardware check on a board is pending.

**Guides:** [install](https://github.com/pravrilgreen/iphone-hid/blob/main/docs/guide/install.md),
[iPhone setup](https://github.com/pravrilgreen/iphone-hid/blob/main/docs/guide/iphone-setup.md),
[hardware check](https://github.com/pravrilgreen/iphone-hid/blob/main/docs/guide/hardware-check.md),
[API](https://github.com/pravrilgreen/iphone-hid/blob/main/docs/dev/api.md).
All changes: [CHANGELOG.md](https://github.com/pravrilgreen/iphone-hid/blob/main/CHANGELOG.md).

Checksums are in `SHA256SUMS`.
