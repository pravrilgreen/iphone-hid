**iphone-hid box: remote touch, keyboard and screen for an iPhone, from an Orange Pi 5 Plus.**

**Install or update** (copy the file to the board, then):

    sudo sh ihc-box-*-linux-arm64.run

The console is at `http://<board address>:8000`; the API token is in `/var/lib/ihc/token`.
Check the board with `ihcd check`. The box is one static binary: it runs on the board's Ubuntu,
Debian or Armbian image and downloads nothing.

**New in this release**

- Touch in real time: drag, swipe, scroll and hold act while the hand moves; every touch lands
  where it was made (absolute pointer).
- Phone buttons: Home, App Switcher, Search, volume, mute, play/pause, wake.
- A new console, with the live touch latency, picture rate and round trip in its top bar.
- A 3.4 MB bundle that replaces the earlier Python version.

**Guides:** [install](https://github.com/pravrilgreen/iphone-hid/blob/main/docs/guide/install.md),
[iPhone setup](https://github.com/pravrilgreen/iphone-hid/blob/main/docs/guide/iphone-setup.md),
[hardware check](https://github.com/pravrilgreen/iphone-hid/blob/main/docs/guide/hardware-check.md),
[API](https://github.com/pravrilgreen/iphone-hid/blob/main/docs/dev/api.md).
All changes: [CHANGELOG.md](https://github.com/pravrilgreen/iphone-hid/blob/main/CHANGELOG.md).

Checksums are in `SHA256SUMS`.
