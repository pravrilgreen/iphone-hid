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
iPhone's screen?): [docs/quick-test.md](https://github.com/pravrilgreen/iphone-hid/blob/main/docs/quick-test.md).
Wiring, checks and troubleshooting: [docs/gadget.md](https://github.com/pravrilgreen/iphone-hid/blob/main/docs/gadget.md).

Checked on a real board: an Orange Pi 5 Plus on Armbian (vendor kernel 6.1) with an iPhone 15 takes
the board's keyboard and mouse. The HDMI input has not been checked on a real board yet: it passed
the test suite and a smoke test of the bundle under ARM64 emulation with an Ubuntu 22.04 user space.
