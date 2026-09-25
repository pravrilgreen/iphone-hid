# The all-in-one box: Orange Pi 5 Plus

One Orange Pi 5 Plus per iPhone does the whole job:

- it **is** the iPhone's keyboard and mouse: its USB-C port runs as a USB device (Linux USB gadget);
- it **sees** the iPhone's screen through its own **HDMI input**;
- it runs the server, the web console and the API.

No HID chip, no capture card. The only other parts are a USB-C hub and cables.

First time with a board? Start with the 10-minute [quick test](quick-test.md) (Vietnamese): does the
iPhone take the board's mouse, with nothing installed.

## What you need

| Part | Notes |
|---|---|
| Orange Pi 5 Plus (v2.x is fine) | Its own 5 V / 4 A USB-C power supply |
| iPhone 15 or later with USB-C | Not 16e/17e: they have no video output |
| USB-C hub: HDMI + USB-A + USB-C PD input | HDMI through DisplayPort Alt Mode (not DisplayLink). Example: UGREEN Revodok 105 (15495) |
| USB-C charger, 30 W or more | Plugged into the hub's PD input: it powers the hub and charges the iPhone |
| HDMI cable | Hub → the board's **HDMI IN** |
| USB-A to USB-C cable | Hub USB-A → the board's **Type-C USB 3.0/DP port** |
| Ethernet cable | For the API and the web console |

## Wiring

1. Charger → the hub's USB-C PD input.
2. Hub HDMI → the board's port labelled **HDMI IN**. The board has two HDMI outputs as well; they
   do not capture.
3. Hub USB-A → **USB-A to USB-C cable** → the board's **Type-C port next to the USB 3 ports**. This
   is not the power port; that one is on the other edge and only takes power.
4. Hub → iPhone. Unlock it, and answer **Allow** if iOS asks about the accessory.
5. Board power last.

Use a **USB-A to USB-C** cable for step 3, not USB-C to USB-C. The USB-A end tells the board that
the other side is a host, so its port turns into a device. With a C-to-C cable both sides can take
either role, and the board, which prefers to be the host, may end up as the host.

## Set up the board

1. Install the board's Ubuntu or Debian image. The Orange Pi image ships the HDMI input driver.
2. Check that the USB-C port can act as a device:

   ```
   ls /sys/class/udc
   ```

   It should print a controller name such as `fc000000.usb`. If it prints nothing, see
   [Troubleshooting](#troubleshooting).
3. Check the HDMI input, with the iPhone connected and unlocked. `ihc-capture-check` comes with
   the install (step 4); `v4l2-ctl --list-devices` works too if the image has v4l-utils:

   ```
   ihc-capture-check list               # look for rk_hdmirx (or snps_hdmirx)
   ```

4. Install the software. Download `ihc-box-<version>-linux-aarch64.run` from the
   [latest release](https://github.com/pravrilgreen/iphone-hid/releases/latest), copy it to the
   board, and run it. It is one self-contained file with its own Python and libraries: it installs
   the box in `/opt/ihc`, sets up the gadget at every boot, then starts the server.

   ```
   chmod +x ihc-box-*-linux-aarch64.run
   sudo ./ihc-box-*-linux-aarch64.run
   ```

   From a source checkout instead: `sudo sh deploy/install.sh` (installs the same services with pip).
   After installing, the commands are `ihc`, `ihc-hidtest` and `ihc-capture-check`. Stop the server
   (`sudo systemctl stop ihc`) before trying them by hand, so two programs do not drive the phone at
   once.

The console is at `http://<board address>:8000`. The phone appears as `iphone-` followed by the last
six characters of the board's serial number (for example `iphone-b40d9e`), so every box of a farm
has its own id. To choose the name, put `IHC_PHONE_ID=iphone15-a01` in `/etc/default/ihc` and restart
the service (`sudo systemctl restart ihc`).

## Check each part by hand

**Keyboard and mouse, first on a laptop.** Plug the board's Type-C port into a laptop with the
USB-A to USB-C cable, USB-A end in the laptop:

```
sudo ihc gadget up                                   # (the installer's service does this at boot)
ihc gadget status                                    # "USB state configured" = the laptop enumerated it
python3 tools/hidtest.py --gadget "info; type hello"
```

The laptop should list a keyboard and mouse named "ihc keyboard + mouse", and `hello` should be
typed into whatever window has focus.

**Then on the iPhone**, through the hub's USB-A port:

```
python3 tools/hidtest.py --gadget "info; abstest"
```

`info` should say `USB connected`. `abstest` sends the absolute pointer to the four corners and the
centre, then asks what you saw. Set up the iPhone first as in [iphone-setup.md](iphone-setup.md)
(AssistiveTouch on).

**Video:**

```
python3 tools/capture_check.py list
python3 tools/capture_check.py probe --device /dev/video0 --seconds 10
python3 tools/capture_check.py snapshot --device /dev/video0
```

## How it works

**Keyboard and mouse.** `ihc gadget up` sets up the kernel's HID function through configfs. It
creates one USB interface per report type: keyboard, media keys, system keys, relative mouse and
absolute pointer. Each interface is a `/dev/hidgN` node. A report counts as delivered only once
the iPhone has polled it. That is a stronger confirmation than a CH9329 gives: the CH9329 only says
it received the command. If the iPhone stops polling (locked, unplugged, accessory not allowed),
the report times out, and the server reports the phone as disconnected.

Profiles:

| Profile | Contents |
|---|---|
| `RA` (default) | Keyboard, media keys, relative mouse and absolute pointer |
| `A` | Absolute pointer only |
| `R` | Relative mouse only |
| `K` | Keyboard only |

Switch with `sudo ihc gadget up --replace --profile A`. Each profile has its own USB serial
number, so iOS does not reuse a descriptor it saw before.

**Video.** The HDMI input delivers raw frames, which become JPEG images: the same frames an MJPEG
capture card sends. Whichever of these is available first does it:

1. the board's hardware JPEG encoder, through GStreamer (`mppjpegenc`), when the image provides it;
2. GStreamer's or ffmpeg's software encoder;
3. the box's own reader. It reads the driver directly and encodes with the OpenCV it ships with, so
   it works on a board with nothing installed and no internet.

At start, the server writes a 1080p60 display description (EDID) to the HDMI input, straight to
the driver, so the iPhone mirrors at 1920×1080 rather than 4K. Video restarts by itself when the
signal goes away or changes. `ihc-capture-check probe --device /dev/video0 --encoder builtin`
(or `mpp`, `gst`) compares them.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `ls /sys/class/udc` prints nothing | The USB-C port is held in host mode by this image's device tree. The mainline Linux device tree declares the port dual-role (`data-role = "dual"`, USB role switch), and Orange Pi uses the port for flashing and ADB, so the hardware can do it. Try Orange Pi's own image. If nothing works, use a CH9329 cable for keyboard and mouse: the box keeps its HDMI input, and the software supports both |
| `ihc gadget up` says the controller is used by another gadget | The image runs its own gadget, usually ADB. `ihc gadget status` names it. Stop it, then run `up` again |
| `ihc gadget status` stays at `not attached` | Wrong cable or wrong port. Use USB-A to USB-C, and the Type-C port next to the USB 3 ports |
| `info` says NOT connected on the iPhone | The iPhone is locked, or it asked to allow the accessory. Unlock it and answer Allow |
| No `rk_hdmirx` video device | This kernel lacks the HDMI input driver. Use the Orange Pi image |
| Video at 4K, or `hdmi_input_edid` with an error in the server log | The driver refused the EDID: replug the HDMI cable and restart the service; if it persists, send the log line |
| Video status "no usable HDMI signal" | The iPhone is locked, or the hub gets no picture out of it: unlock it, replug the hub |

Farm config form, when you do not use `--auto`:

```toml
[[device]]
id = "iphone"
model = "iPhone 15"
hid = { gadget = "ihc" }
video = { device = "/dev/video0", input = "hdmi", fps = 30 }
calibration = "calib/iphone.json"
```
