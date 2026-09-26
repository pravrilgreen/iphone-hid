# Install a box: Orange Pi 5 Plus

One Orange Pi 5 Plus per iPhone. It is the iPhone's touch pointer and keyboard through its USB-C
port, it reads the iPhone's screen from its HDMI input, and it serves the API and the console.

## Parts

| Part | Notes |
|---|---|
| Orange Pi 5 Plus (v2.x is fine) | With its own 5 V / 4 A USB-C supply |
| iPhone 15 or later with USB-C | Not 16e or 17e: they have no video output |
| USB-C hub: HDMI, USB-A, USB-C PD input | HDMI through DisplayPort Alt Mode. Example: UGREEN Revodok 105 (15495) |
| USB-C charger, 30 W or more | Into the hub's PD input: powers the hub, charges the phone |
| HDMI cable | Hub to the board's **HDMI IN** |
| USB-A to USB-C cable, with data | Hub's USB-A to the board's **Type-C USB 3.0/DP** port |
| Ethernet cable | API and console |

## Wiring

1. Charger into the hub's USB-C PD input.
2. Hub HDMI into the board's port labelled **HDMI IN** (the two other HDMI ports are outputs).
3. Hub USB-A, through the USB-A to USB-C cable, into the board's **Type-C port next to the USB 3
   ports**. The power port is on the other edge.
4. Hub to the iPhone. Unlock the phone and answer **Allow** if iOS asks about the accessory.
5. Board power last.

The USB-A end of the cable in step 3 tells the board that the other side is the host, so its port
becomes a device. With a USB-C to USB-C cable the board may take the host role itself.

## Board image and HDMI input

Use the board's Ubuntu or Debian image. The Orange Pi image turns the HDMI input on. Armbian (vendor
kernel 6.1) has the driver built in but leaves the HDMI input off in the device tree, and ships the
`rk3588-hdmirx` overlay that turns it on.

`ihcd check` (after installing) reads the device tree, the kernel and the boot scripts and says what
this board needs. The two usual cases on Armbian:

- **Armbian 24.11 or later:** add `rk3588-hdmirx` to the `overlays=` line of `/boot/armbianEnv.txt`
  (one line, names separated by spaces), then reboot.
- **Earlier boot scripts** load `overlays=` entries only as `<overlay_prefix>-<entry>.dtbo`, so
  `rk3588-hdmirx` is skipped. Load it as a user overlay instead:

  ```sh
  sudo mkdir -p /boot/overlay-user
  sudo cp /boot/dtb/rockchip/overlay/rk3588-hdmirx.dtbo /boot/overlay-user/
  echo 'user_overlays=rk3588-hdmirx' | sudo tee -a /boot/armbianEnv.txt
  sudo reboot
  ```

## Install the box software

Download `ihc-box-<version>-linux-arm64.run` from the
[latest release](https://github.com/pravrilgreen/iphone-hid/releases/latest), copy it to the board,
then:

```sh
sudo sh ihc-box-*-linux-arm64.run
```

It installs `/opt/ihc/bin/ihcd` (also on the path as `ihcd`), the udev rules, an API token in
`/var/lib/ihc/token`, and two services:

- `ihcd-gadget` makes the board's USB-C port the iPhone's touch pointer and keyboard at boot;
- `ihcd` serves the console and the API on port 8000.

Updating is the same command with the newer file; the token stays. An earlier box version (the
services `ihc` and `ihc-gadget`) is replaced.

The console is at `http://<board address>:8000` and asks for the token once
(`sudo cat /var/lib/ihc/token`). The phone is named `iphone-` followed by six characters derived from
the board's serial number, so every box of a farm has its own name.

Settings go in `/etc/default/ihc`, then `sudo systemctl restart ihcd`:

```sh
IHCD_ARGS="--id iphone-a01"            # the phone's name on the network
IHCD_ARGS="--landscape"                 # the phone mirrors in landscape
IHCD_ARGS="--settle 120ms"              # wait longer before a press that follows a jump
```

`ihcd serve -h` lists every flag.

## Check it

```sh
ihcd check                 # USB device controller, gadget, HDMI input and its signal
ihcd gadget status         # "state": "configured" once the iPhone has taken the gadget
```

Then run the [hardware check](hardware-check.md) on the iPhone: the pointer, the buttons, the
video. Stop the service while driving the phone by hand (`sudo systemctl stop ihcd`), so two
programs do not drive it at once.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `ihcd check` finds no USB device controller | The image's device tree keeps the USB-C port in host mode. The Orange Pi image and Armbian's vendor image allow device mode on the Type-C port next to the USB 3 ports |
| `ihcd gadget up` says the controller is used by another gadget | The image runs its own gadget, usually ADB. `ihcd gadget status` names it: unbind it, then `sudo systemctl restart ihcd-gadget` |
| The gadget stays at `not attached` | Wrong cable or port: USB-A to USB-C, into the Type-C port next to the USB 3 ports |
| The console says **Not connected** | The iPhone is locked or waits for **Allow** for the accessory. Unlock it and allow it |
| The console says **Asleep** | Auto-Lock put the phone to sleep. Press **Wake** (or the side button of the drawn phone), and set Auto-Lock to Never ([iPhone setup](iphone-setup.md)) |
| No HDMI input in `ihcd check` | The HDMI input is off in the device tree: `ihcd check` says which overlay step this board needs |
| **No picture** while the HDMI input exists | The phone is locked, or the hub gets no picture from it: unlock it, replug the hub |
| The pointer does not move | AssistiveTouch is off, or the phone kept an older descriptor: `sudo ihcd gadget up --replace --profile A`, then `--profile RA` again |
