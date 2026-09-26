#!/bin/sh
# Smoke-test a box bundle: unpack it, start ihcd with a simulated iPhone, and check the API, a tap
# and a screenshot. On another architecture it runs under qemu-user (qemu-aarch64).
#   scripts/check_box.sh dist/ihc-box-<version>-linux-arm64.run
set -eu

RUN="${1:?usage: $0 dist/ihc-box-<version>-linux-<arch>.run}"
WORK="$(mktemp -d)"
trap 'kill "$PID" 2>/dev/null || true; rm -rf "$WORK"' EXIT
PID=""
IHC_ANY_ARCH=1 sh "$RUN" --extract "$WORK" > /dev/null
BIN="$WORK/bin/ihcd"
case "$(file -b "$BIN")" in
    *aarch64*) [ "$(uname -m)" = aarch64 ] || BIN="qemu-aarch64 $BIN" ;;
    *x86-64*) [ "$(uname -m)" = x86_64 ] || BIN="qemu-x86_64 $BIN" ;;
esac
$BIN version
PORT=18765
TOKEN=smoke
$BIN serve --sim --addr "127.0.0.1:$PORT" --token "$TOKEN" --no-mdns > "$WORK/log" 2>&1 &
PID=$!
URL="http://127.0.0.1:$PORT"
for i in $(seq 100); do
    curl -sf "$URL/api/health" > /dev/null 2>&1 && break
    sleep 0.2
done
curl -sf "$URL/api/health" || { cat "$WORK/log"; exit 1; }
echo
AUTH="Authorization: Bearer $TOKEN"
for i in $(seq 300); do  # the first frame takes a while under emulation
    curl -sf -H "$AUTH" "$URL/api/devices/iphone-sim" | grep -q '"state":"ready"' && break
    sleep 0.2
done
curl -sf -H "$AUTH" "$URL/api/devices/iphone-sim" | grep -q '"state":"ready"' \
    || { echo "not ready:"; curl -s -H "$AUTH" "$URL/api/devices/iphone-sim"; exit 1; }
curl -sf -H "$AUTH" -H "Content-Type: application/json" -d '{"x":0.374,"y":0.142}' "$URL/api/devices/iphone-sim/tap" \
    | grep -q '"ok":true' || { echo "tap failed"; exit 1; }
sleep 1
curl -sf -H "$AUTH" "$URL/api/devices/iphone-sim/sim" | grep -q '"app":"Notes"' \
    || { echo "the tap did not open Notes:"; curl -s -H "$AUTH" "$URL/api/devices/iphone-sim/sim"; exit 1; }
curl -sf -H "$AUTH" -o "$WORK/shot.jpg" "$URL/api/devices/iphone-sim/screenshot?format=jpeg&wait=true"
[ "$(head -c 2 "$WORK/shot.jpg" | od -An -tx1 | tr -d ' ')" = ffd8 ] || { echo "the screenshot is not a JPEG"; exit 1; }
curl -sf "$URL/" | grep -q "<canvas id=\"screen\"" || { echo "no console"; exit 1; }
echo "bundle OK"
