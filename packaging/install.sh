#!/bin/sh
# Install or update the iphone-hid box from an unpacked bundle: /opt/ihc/bin/ihcd, the udev rules,
# the API token and the services. Run as root from the bundle directory (the .run file does it).
#   --no-start   install everything, start at the next boot
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

id ihc >/dev/null 2>&1 || useradd --system --home-dir /var/lib/ihc --shell /usr/sbin/nologin ihc
usermod -aG video ihc

# an earlier box version ran as the services ihc and ihc-gadget, with its own Python in /opt/ihc
for old in ihc ihc-gadget; do
    if [ -f "/etc/systemd/system/$old.service" ]; then
        systemctl disable --now "$old" >/dev/null 2>&1 || true
        rm -f "/etc/systemd/system/$old.service"
    fi
done
for cmd in ihc ihc-hidtest ihc-capture-check; do
    [ -L "/usr/local/bin/$cmd" ] && case "$(readlink "/usr/local/bin/$cmd")" in /opt/ihc/*) rm -f "/usr/local/bin/$cmd" ;; esac
done

systemctl stop ihcd 2>/dev/null || true
rm -rf "$PREFIX.new"
mkdir -p "$PREFIX.new/bin"
install -m 755 "$SRC/bin/ihcd" "$PREFIX.new/bin/ihcd"
cp "$SRC/VERSION" "$SRC/THIRD_PARTY.md" "$PREFIX.new/"
rm -rf "$PREFIX.old"
[ -e "$PREFIX" ] && mv "$PREFIX" "$PREFIX.old"
mv "$PREFIX.new" "$PREFIX"
rm -rf "$PREFIX.old"
ln -sf "$PREFIX/bin/ihcd" /usr/local/bin/ihcd

install -m 644 "$SRC/99-ihc.rules" /etc/udev/rules.d/99-ihc.rules
udevadm control --reload 2>/dev/null || true
udevadm trigger 2>/dev/null || true

# the API token: readable by the service only; kept across updates unless IHC_TOKEN is given
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

for unit in ihcd.service ihcd-gadget.service; do
    install -m 644 "$SRC/$unit" "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemctl enable ihcd ihcd-gadget >/dev/null
if [ "$START" = 1 ]; then
    if [ -n "$(ls /sys/class/udc 2>/dev/null)" ]; then
        systemctl restart ihcd-gadget || echo "warning: the USB gadget did not start: journalctl -u ihcd-gadget"
    else
        echo "note: no USB device controller (ls /sys/class/udc is empty): the board's USB-C port is in host mode"
    fi
    systemctl restart ihcd
fi

ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo
echo "iphone-hid box $(cat "$PREFIX/VERSION") installed"
[ "$START" = 1 ] && echo "console: http://${ip:-<board address>}:8000     logs: journalctl -u ihcd -f"
echo "API token (the console asks for it once): sudo cat $TOKEN"
echo "check the board: ihcd check"
