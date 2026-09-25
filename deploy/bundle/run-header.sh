#!/bin/sh
# iphone-hid box for the Orange Pi 5 Plus (Linux, ARM64): one self-contained file with its own
# Python and libraries.
#
#   sudo ./ihc-box.run                 install or update in /opt/ihc and start the service
#   sudo ./ihc-box.run --no-start      install or update, start only at the next boot
#   ./ihc-box.run --extract DIR        only unpack into DIR (then: DIR/bin/ihc ..., DIR/bin/ihc-hidtest ...)
#   ./ihc-box.run --version
set -eu

VERSION="@VERSION@"
case "${1:-}" in
    --version) echo "iphone-hid $VERSION (linux-aarch64)"; exit 0 ;;
    -h|--help) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
esac

arch="$(uname -m)"
if [ "$arch" != aarch64 ] && [ -z "${IHC_ANY_ARCH:-}" ]; then
    echo "this bundle is for ARM64 Linux (Orange Pi 5 Plus); this machine is $arch" >&2
    exit 1
fi
command -v xz >/dev/null 2>&1 || { echo "xz is needed to unpack: sudo apt install xz-utils" >&2; exit 1; }

payload() {
    line="$(awk '/^__IHC_PAYLOAD_BELOW__$/ { print NR + 1; exit }' "$0")"
    tail -n "+$line" "$0" | xz -dc | tar -x -C "$1"
}

if [ "${1:-}" = "--extract" ]; then
    dest="${2:?usage: $0 --extract DIR}"
    mkdir -p "$dest"
    payload "$dest"
    echo "unpacked into $dest: try $dest/bin/ihc gadget status"
    exit 0
fi

if [ "$(id -u)" != 0 ]; then
    echo "to install: sudo $0        (or $0 --extract DIR to only unpack; $0 --help)" >&2
    exit 1
fi
work="$(mktemp -d /tmp/ihc-box.XXXXXX)"
trap 'rm -rf "$work"' EXIT
echo "unpacking iphone-hid $VERSION ..."
payload "$work"
sh "$work/install.sh" "$@"
exit 0
__IHC_PAYLOAD_BELOW__
