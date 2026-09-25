#!/usr/bin/env bash
# Build the self-contained box bundle for ARM64 Linux (Orange Pi 5 Plus):
#
#     scripts/build_bundle.sh          # -> dist/ihc-box-<version>-linux-aarch64.run
#
# One file with its own Python (python-build-standalone), every library as an ARM64 wheel, the app,
# launchers and the installer. On the board: `sudo ./ihc-box-...run` installs /opt/ihc and starts the
# service; nothing is installed with pip there. Runs on any Linux host with Python 3, pip, curl, tar
# and xz (the wheels are fetched for the target platform, nothing is compiled).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${OUT:-$ROOT/dist}"
CACHE="${IHC_BUILD_CACHE:-$ROOT/.cache/bundle}"
HOST_PY="${HOST_PY:-python3}"

PBS_RELEASE=20250918
PY_VERSION=3.11.13
PY_TGZ="cpython-${PY_VERSION}+${PBS_RELEASE}-aarch64-unknown-linux-gnu-install_only.tar.gz"
PY_SHA256=854333f8768571f138ae2edca969d85894d7c3824e1516f693b22338ab7f64d5
PY_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_RELEASE}/${PY_TGZ}"

# glibc 2.28 at most: Debian 11 / Ubuntu 20.04 and newer board images
PLATFORMS=(--platform manylinux2014_aarch64 --platform manylinux_2_28_aarch64)

version="$(sed -n 's/^version = "\(.*\)"/\1/p' "$ROOT/pyproject.toml")"
rev="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
if [ -n "$(git -C "$ROOT" status --porcelain -- src packaging 2>/dev/null)" ]; then
    rev="$rev-dirty"
fi
VERSION="$version+$rev"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
box="$work/ihc-box"
mkdir -p "$box" "$CACHE" "$OUT"

echo "== Python $PY_VERSION for aarch64"
if [ ! -f "$CACHE/$PY_TGZ" ]; then
    curl -fL --retry 3 -o "$CACHE/$PY_TGZ.part" "$PY_URL"
    mv "$CACHE/$PY_TGZ.part" "$CACHE/$PY_TGZ"
fi
echo "$PY_SHA256  $CACHE/$PY_TGZ" | sha256sum -c --quiet
tar -xzf "$CACHE/$PY_TGZ" -C "$box"  # -> python/
rm -rf "$box/python/lib/python3.11/test" "$box/python/lib/python3.11/idlelib" \
       "$box/python/lib/python3.11/tkinter" "$box/python/lib/python3.11/turtledemo" \
       "$box/python/lib/python3.11/ensurepip" "$box/python/lib/python3.11/lib2to3" \
       "$box/python/lib/libtcl"* "$box/python/lib/libtk"* "$box/python/lib/tcl"* "$box/python/lib/tk"* \
       "$box/python/lib/itcl"* "$box/python/lib/thread"* "$box/python/share"

echo "== libraries (ARM64 wheels)"
"$HOST_PY" -m pip install --quiet --disable-pip-version-check --no-compile --target "$box/lib" \
    "${PLATFORMS[@]}" --only-binary=:all: --python-version 3.11 --implementation cp \
    -r "$ROOT/packaging/bundle/requirements.txt"
find "$box/lib" -name tests -type d -path "*/numpy/*" -prune -exec rm -rf {} +
rm -rf "$box/lib/bin"

echo "== app"
mkdir -p "$box/app/docs" "$box/bin"
cp -r "$ROOT/src/ihc" "$ROOT/packaging" "$box/app/"  # the package carries its console (web/) and lab tools
rm -rf "$box/app/packaging/bundle"
cp "$ROOT/docs/guide/orange-pi-box.md" "$box/app/docs/"
find "$box/app" -name __pycache__ -type d -prune -exec rm -rf {} +
echo "$VERSION" > "$box/VERSION"
cp "$ROOT/packaging/bundle/install.sh" "$box/install.sh"
cp "$ROOT/packaging/bundle/launcher.sh" "$box/bin/ihc"
chmod 755 "$box/bin/ihc" "$box/install.sh"
ln -s ihc "$box/bin/ihc-hidtest"
ln -s ihc "$box/bin/ihc-capture-check"

echo "== bytecode"
if "$HOST_PY" -c 'import sys; sys.exit(sys.version_info[:2] != (3, 11))'; then
    "$HOST_PY" -m compileall -q -j 0 -d /opt/ihc/app "$box/app" >/dev/null
    "$HOST_PY" -m compileall -q -j 0 -d /opt/ihc/lib "$box/lib" >/dev/null || true
else
    echo "(host Python is not 3.11: bytecode is compiled on the board at first run)"
fi

echo "== pack"
run="$OUT/ihc-box-$version-linux-aarch64.run"
sed "s/@VERSION@/$VERSION/" "$ROOT/packaging/bundle/run-header.sh" > "$run.part"
tar -C "$box" -cf - . | xz -T0 -6 >> "$run.part"
chmod 755 "$run.part"
mv "$run.part" "$run"
echo "built $run ($(du -h "$run" | cut -f1), iphone-hid $VERSION)"
