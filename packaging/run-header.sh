#!/bin/sh
# iphone-hid box software for ARM64 Linux boards (Orange Pi 5 Plus): one file, nothing to download.
#
#   sudo ./ihc-box.run                 install or update, then start the box
#   sudo ./ihc-box.run --no-start      install or update, start at the next boot
#   ./ihc-box.run --extract DIR        only unpack into DIR (then: DIR/bin/ihcd ...)
#   ./ihc-box.run --version
set -eu

VERSION="@VERSION@"
ARCH="@ARCH@"
case "${1:-}" in
    --version) echo "iphone-hid box $VERSION (linux-$ARCH)"; exit 0 ;;
    -h|--help) sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
esac

machine="$(uname -m)"
case "$ARCH:$machine" in
    arm64:aarch64|amd64:x86_64) ;;
    *) [ -n "${IHC_ANY_ARCH:-}" ] || { echo "this file is for $ARCH Linux; this machine is $machine" >&2; exit 1; } ;;
esac

payload() {
    line="$(awk '/^__IHC_PAYLOAD_BELOW__$/ { print NR + 1; exit }' "$0")"
    quiet=""
    tar --version 2>/dev/null | grep -q GNU && quiet="--warning=no-timestamp"  # a board clock may run behind
    tail -n "+$line" "$0" | gzip -dc | tar -x $quiet -C "$1"
}

if [ "${1:-}" = "--extract" ]; then
    dest="${2:?usage: $0 --extract DIR}"
    mkdir -p "$dest"
    payload "$dest"
    echo "unpacked into $dest: try $dest/bin/ihcd doctor"
    exit 0
fi
if [ "$(id -u)" != 0 ]; then
    echo "to install: sudo $0     (or $0 --extract DIR to only unpack; $0 --help)" >&2
    exit 1
fi
work="$(mktemp -d /tmp/ihc-box.XXXXXX)"
trap 'rm -rf "$work"' EXIT
echo "unpacking iphone-hid box $VERSION ..."
payload "$work"
sh "$work/install.sh" "$@"
exit 0
__IHC_PAYLOAD_BELOW__
