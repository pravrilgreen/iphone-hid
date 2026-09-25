#!/bin/sh
# Install (or update) the self-contained iphone-hid box from an unpacked bundle: /opt/ihc, the
# commands ihc / ihc-hidtest / ihc-capture-check, the udev rules, the API token and the services.
# Run as root from the bundle directory (the .run file does this for you). Options:
#   --no-start   install everything but do not start the services now (they still start at boot)
set -eu

START=1
for arg in "$@"; do
    case "$arg" in
        --no-start) START=0 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done
[ "$(id -u)" = 0 ] || { echo "run this as root (sudo)" >&2; exit 1; }

SRC="$(cd "$(dirname "$0")" && pwd)"
PREFIX=/opt/ihc

# System packages the bundle cannot carry: GStreamer reads the HDMI input, v4l-utils sets its EDID.
# (Board images usually have them; the Rockchip GStreamer plugin adds the hardware JPEG encoder.)
missing=""
command -v gst-launch-1.0 >/dev/null 2>&1 || missing="$missing gstreamer1.0-tools gstreamer1.0-plugins-base gstreamer1.0-plugins-good"
command -v v4l2-ctl >/dev/null 2>&1 || missing="$missing v4l-utils"
if [ -n "$missing" ]; then
    echo "installing:$missing"
    if ! (apt-get install -y $missing || { apt-get update && apt-get install -y $missing; }); then
        echo "warning: could not install$missing (no network?): the HDMI input will not work until they are installed"
    fi
fi

id ihc >/dev/null 2>&1 || useradd --system --home-dir /var/lib/ihc --shell /usr/sbin/nologin ihc
usermod -aG dialout,video,input ihc

# replace the install in one step, so a failed copy never leaves half an install behind
systemctl stop ihc 2>/dev/null || true
rm -rf "$PREFIX.new"
mkdir -p "$PREFIX.new"
cp -a "$SRC/." "$PREFIX.new/"
rm -rf "$PREFIX.old"
[ -e "$PREFIX" ] && mv "$PREFIX" "$PREFIX.old"
mv "$PREFIX.new" "$PREFIX"
rm -rf "$PREFIX.old"
for cmd in ihc ihc-hidtest ihc-capture-check; do
    ln -sf "$PREFIX/bin/$cmd" "/usr/local/bin/$cmd"
done

install -m 644 "$PREFIX/app/deploy/99-ihc.rules" /etc/udev/rules.d/99-ihc.rules
udevadm control --reload 2>/dev/null || true
udevadm trigger 2>/dev/null || true

# API token: readable by the service only; kept across updates unless IHC_TOKEN is given
TOKEN=/var/lib/ihc/token
install -d -m 750 -o ihc -g ihc /var/lib/ihc
if [ -n "${IHC_TOKEN:-}" ] || [ ! -s "$TOKEN" ]; then
    (
        umask 077
        if [ -n "${IHC_TOKEN:-}" ]; then
            printf '%s\n' "$IHC_TOKEN" > "$TOKEN"
        else
            head -c 18 /dev/urandom | base64 | tr '+/' '-_' > "$TOKEN"
        fi
    )
fi
chown ihc:ihc "$TOKEN"
chmod 600 "$TOKEN"

# the same unit files as a source install, pointed at the bundle's launcher
for unit in ihc.service ihc-gadget.service; do
    sed 's#/opt/ihc/.venv/bin/ihc#/opt/ihc/bin/ihc#g' "$PREFIX/app/deploy/$unit" > "/etc/systemd/system/$unit"
    chmod 644 "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemctl enable ihc >/dev/null
if [ -n "$(ls /sys/class/udc 2>/dev/null)" ]; then
    systemctl enable ihc-gadget >/dev/null
    if [ "$START" = 1 ]; then
        systemctl restart ihc-gadget || echo "warning: the USB gadget did not start: journalctl -u ihc-gadget"
    fi
else
    echo "note: no USB device controller on this board (ls /sys/class/udc is empty): the USB-C port cannot"
    echo "      be the keyboard and mouse with this image; a CH9329 cable works instead (see docs/gadget.md)"
fi
if [ "$START" = 1 ]; then
    systemctl restart ihc
fi

ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo
echo "iphone-hid $(cat "$PREFIX/VERSION") installed in $PREFIX"
[ "$START" = 1 ] && echo "console: http://${ip:-<board address>}:8000   (logs: journalctl -u ihc -f)"
echo "API token (the console asks for it once):  sudo cat $TOKEN"
echo "commands: ihc gadget status | ihc-hidtest --gadget | ihc-capture-check list"
echo "          (stop the server first when testing by hand: sudo systemctl stop ihc)"
