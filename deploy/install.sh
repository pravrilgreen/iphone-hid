#!/bin/sh
# Install iphone-hid as a plug-and-play box on a Debian/Ubuntu/Raspberry Pi OS host.
# Run from the repository root: sudo sh deploy/install.sh
set -eu

PREFIX=/opt/ihc
SRC="$(cd "$(dirname "$0")/.." && pwd)"

apt-get install -y python3-venv v4l-utils usbutils

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

install -m 644 "$SRC/deploy/ihc.service" /etc/systemd/system/ihc.service
systemctl daemon-reload
systemctl enable --now ihc

echo "iphone-hid is running: http://$(hostname -I | awk '{print $1}'):8000  (logs: journalctl -u ihc -f)"
