**The all-in-one box for the Orange Pi 5 Plus, in one file.** It carries its own Python and every
library, and reads the HDMI input and sets its EDID by itself, so **the board needs no internet and
nothing installed**. For ARM64 Linux: the board's Ubuntu 22.04 or Debian 11 image, or newer. When the
board image has GStreamer with the Rockchip plugin, its hardware JPEG encoder is used.

**Install or update** (copy the `.run` file to the board, then):

    chmod +x ihc-box-*-linux-aarch64.run
    sudo ./ihc-box-*-linux-aarch64.run

This installs everything and starts it:

- the box itself in `/opt/ihc`;
- the commands `ihc`, `ihc-hidtest` and `ihc-capture-check`;
- the udev rules, and an API token in `/var/lib/ihc/token`;
- two services: `ihc-gadget` makes the board the iPhone's USB keyboard and mouse at boot, and `ihc`
  runs the server.

The console is then at `http://<board address>:8000`.

- `sudo ./ihc-box-...run --no-start` installs without starting the services now.
- `./ihc-box-...run --extract DIR` only unpacks, to try the tools without installing (`DIR/bin/ihc
  gadget status`).

First checks, nothing installed (does the iPhone take the board's mouse, does the board capture the
iPhone's screen?): [docs/guide/quick-test.md](https://github.com/pravrilgreen/iphone-hid/blob/main/docs/guide/quick-test.md).
Wiring, checks and troubleshooting: [docs/guide/orange-pi-box.md](https://github.com/pravrilgreen/iphone-hid/blob/main/docs/guide/orange-pi-box.md).

**Status on real hardware** (iPhone 15, Orange Pi 5 Plus with Armbian, vendor kernel 6.1):

- The iPhone follows both the relative mouse and the absolute pointer. Pick **Absolute** under
  Pointer in the console (or `ihc serve --pointer absolute`, or `IHC_POINTER=absolute` in
  `/etc/default/ihc`): clicks on the live view land where you click, with no calibration.
- Armbian leaves the HDMI input off in the board's device tree. Turn it on with its `rk3588-hdmirx`
  overlay (docs/guide/orange-pi-box.md, "Set up the board"). `ihc-capture-check list` says what a
  board lacks and how its boot script takes the overlay.
- A sleeping iPhone suspends the USB bus and takes no input: set Auto-Lock to Never
  (docs/guide/iphone-setup.md). `ihc gadget wake` wakes a sleeping phone; run
  `sudo ihc gadget up --replace` once first. Test B9 in docs/research/phase0-checklist.md checks
  what the iPhone does with it.
- The HDMI input path has passed the test suite and a smoke test of the bundle under ARM64 emulation
  with an Ubuntu 22.04 user space, but has not been checked on a real board yet.

What changed in each version: [CHANGELOG.md](https://github.com/pravrilgreen/iphone-hid/blob/main/CHANGELOG.md).
