#!/bin/sh
# Install iphone-hid as a plug-and-play box on a Debian/Ubuntu/Raspberry Pi OS host.
# Run from the repository root: sudo sh deploy/install.sh
# The API token is made once, in /var/lib/ihc/token; to give several boxes the same one:
#   sudo IHC_TOKEN=<token> sh deploy/install.sh   (replaces the box's token)
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
systemctl daemon-reload
systemctl enable ihc
systemctl restart ihc

echo "iphone-hid is running: http://$(hostname -I | awk '{print $1}'):8000  (logs: journalctl -u ihc -f)"
echo "API token (the console asks for it once; SDK: Farm(..., token=...) or IHC_TOKEN):"
echo "  sudo cat $TOKEN"
