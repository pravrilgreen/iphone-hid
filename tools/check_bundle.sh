#!/usr/bin/env bash
# Smoke-test a built box bundle: unpack it, then run its launchers, its libraries and a simulated
# server with the bundle's own ARM64 Python (under qemu-aarch64 when this host is not ARM64:
# apt install qemu-user libc6-arm64-cross).
#
#     tools/check_bundle.sh dist/ihc-box-*-linux-aarch64.run
set -euo pipefail

run_file="$(readlink -f "$1")"
dir="$(mktemp -d)"
server=""
cleanup() {
    [ -n "$server" ] && kill "$server" 2>/dev/null || true
    rm -rf "$dir"
}
trap cleanup EXIT

IHC_ANY_ARCH=1 sh "$run_file" --version
IHC_ANY_ARCH=1 sh "$run_file" --extract "$dir" >/dev/null
if [ "$(uname -m)" != aarch64 ]; then
    export IHC_PYTHON="qemu-aarch64 -L ${QEMU_LD_PREFIX:-/usr/aarch64-linux-gnu} $dir/python/bin/python3"
fi
PY="${IHC_PYTHON:-$dir/python/bin/python3}"

echo "== libraries"
PYTHONPATH="$dir/app:$dir/lib" PYTHONNOUSERSITE=1 $PY -c '
import platform, cv2, numpy, PIL, fastapi, uvicorn, httptools, uvloop, websockets, zeroconf, serial, httpx
import ihc.api, ihc.cli, ihc.hid.gadget, ihc.video.pipe
print(platform.machine(), platform.python_version(), "opencv", cv2.__version__, "numpy", numpy.__version__)
ok, jpg = cv2.imencode(".jpg", numpy.zeros((8, 8, 3), numpy.uint8))
assert ok and cv2.imdecode(jpg, cv2.IMREAD_COLOR).shape == (8, 8, 3)
'

echo "== launchers"
"$dir/bin/ihc" --help >/dev/null
"$dir/bin/ihc" gadget status
"$dir/bin/ihc-capture-check" list
"$dir/bin/ihc-hidtest" --fake --no-log "info; move 20 0; type hi"

echo "== server with one simulated iPhone"
port=18765
"$dir/bin/ihc" serve --sim 1 --host 127.0.0.1 --port "$port" --token check --no-monitor --log-level warning &
server=$!
for _ in $(seq 180); do
    if curl -sf -H "Authorization: Bearer check" "http://127.0.0.1:$port/api/devices" >"$dir/devices.json" 2>/dev/null; then
        break
    fi
    kill -0 "$server" 2>/dev/null || { echo "the server exited" >&2; exit 1; }
    sleep 1
done
grep -q '"sim-01"' "$dir/devices.json" || { echo "no device in the API answer" >&2; exit 1; }
curl -sf -o /dev/null -X POST -H "Authorization: Bearer check" -H "Content-Type: application/json" \
    -d '{"x": 0.5, "y": 0.5}' "http://127.0.0.1:$port/api/devices/sim-01/tap"
curl -sf -o "$dir/shot.jpg" -H "Authorization: Bearer check" "http://127.0.0.1:$port/api/devices/sim-01/screenshot"
[ "$(head -c 2 "$dir/shot.jpg" | od -An -tx1 | tr -d ' ')" = ffd8 ] || { echo "screenshot is not a JPEG" >&2; exit 1; }
echo "bundle OK"
