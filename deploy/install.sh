#!/bin/sh
# Install iphone-hid as a plug-and-play box on a Debian/Ubuntu host: the Orange Pi 5 Plus all-in-one
# box (its USB-C port is the iPhone's keyboard and mouse, its HDMI input the video), or any Linux
# host with CH9329 cables and USB capture cards.
# Run from the repository root: sudo sh deploy/install.sh
# The API token is made once, in /var/lib/ihc/token; to give several boxes the same one:
#   sudo IHC_TOKEN=<token> sh deploy/install.sh   (replaces the box's token)
set -eu

PREFIX=/opt/ihc
SRC="$(cd "$(dirname "$0")/.." && pwd)"

apt-get install -y python3-venv v4l-utils usbutils
# the HDMI input is read through GStreamer (the board image's Rockchip plugin adds the hardware
# JPEG encoder; without it the software one is used)
apt-get install -y gstreamer1.0-tools gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
    || echo "warning: GStreamer not installed: the HDMI input will not work (USB capture cards will)"

id ihc >/dev/null 2>&1 || useradd --system --home-dir /var/lib/ihc --shell /usr/sbin/nologin ihc
usermod -aG dialout,video,input ihc

mkdir -p "$PREFIX"
rm -rf "$PREFIX/src"
cp -r "$SRC" "$PREFIX/src"
python3 -m venv "$PREFIX/.venv"
"$PREFIX/.venv/bin/pip" install --upgrade pip
"$PREFIX/.venv/bin/pip" install "$PREFIX/src[video,api,box]"

install -m 644 "$SRC/deploy/99-ihc.rules" /etc/udev/rules.d/99-ihc.rules
udevadm control --reload
udevadm trigger

# API token: readable by the service only; kept across reinstalls unless IHC_TOKEN is given
TOKEN=/var/lib/ihc/token
install -d -m 750 -o ihc -g ihc /var/lib/ihc
if [ -n "${IHC_TOKEN:-}" ] || [ ! -s "$TOKEN" ]; then
    (
        umask 077
        if [ -n "${IHC_TOKEN:-}" ]; then
            printf '%s\n' "$IHC_TOKEN" > "$TOKEN"
        else
            python3 -c 'import secrets; print(secrets.token_urlsafe(24))' > "$TOKEN"
        fi
    )
fi
chown ihc:ihc "$TOKEN"
chmod 600 "$TOKEN"

install -m 644 "$SRC/deploy/ihc.service" /etc/systemd/system/ihc.service
install -m 644 "$SRC/deploy/ihc-gadget.service" /etc/systemd/system/ihc-gadget.service
systemctl daemon-reload
if [ -n "$(ls /sys/class/udc 2>/dev/null)" ]; then
    # this board has a device-capable USB port: it is the iPhone's keyboard and mouse itself
    systemctl enable ihc-gadget
    systemctl restart ihc-gadget || echo "warning: the USB gadget did not start: sudo journalctl -u ihc-gadget"
else
    echo "note: no USB device controller on this board: use CH9329 cables for keyboard and mouse (docs/gadget.md)"
fi
systemctl enable ihc
systemctl restart ihc

echo "iphone-hid is running: http://$(hostname -I | awk '{print $1}'):8000  (logs: journalctl -u ihc -f)"
echo "API token (the console asks for it once; SDK: Farm(..., token=...) or IHC_TOKEN):"
echo "  sudo cat $TOKEN"
