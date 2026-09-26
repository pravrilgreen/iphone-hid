#!/bin/sh
# Build the box software: a static ihcd for Linux (arm64 by default, ARCH=amd64 for x86-64) with
# libjpeg-turbo, and the self-installing bundle dist/ihc-box-<version>-linux-<arch>.run.
#
# Needs Go, cmake, make and zig 0.13 as the C cross compiler (pip install ziglang==0.13.0; set ZIG
# to another zig command if needed). libjpeg-turbo is downloaded once into build/.
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ARCH="${ARCH:-arm64}"
ZIG="${ZIG:-python3 -m ziglang}"
LJT_VERSION=3.1.2
VERSION="$(cat "$ROOT/VERSION")"
case "$ARCH" in
    arm64) TARGET=aarch64-linux-musl; CPU=aarch64 ;;
    amd64) TARGET=x86_64-linux-musl; CPU=x86_64 ;;
    *) echo "ARCH must be arm64 or amd64" >&2; exit 2 ;;
esac
BUILD="$ROOT/build/$ARCH"
mkdir -p "$BUILD" "$ROOT/dist"

# a C compiler for the target: zig's clang with musl, so the binary is fully static. zig's linker
# refuses -v and --dependency-file, which CMake 4 passes to linkers: leave them out.
cat > "$BUILD/zig-cc" <<SH
#!/bin/sh
for a do
    shift
    case "\$a" in -Wl,-v|-Wl,--dependency-file=*) continue ;; esac
    set -- "\$@" "\$a"
done
exec $ZIG cc -target $TARGET "\$@"
SH
for tool in ar ranlib; do
    printf '#!/bin/sh\nexec %s %s "$@"\n' "$ZIG" "$tool" > "$BUILD/zig-$tool"
done
chmod +x "$BUILD/zig-cc" "$BUILD/zig-ar" "$BUILD/zig-ranlib"

# libjpeg-turbo, static
LJT="$BUILD/libjpeg-turbo"
if [ ! -f "$LJT/lib/libturbojpeg.a" ]; then
    SRC="$ROOT/build/libjpeg-turbo-$LJT_VERSION"
    if [ ! -d "$SRC" ]; then
        curl -sSfL -o "$ROOT/build/ljt.tar.gz" \
            "https://github.com/libjpeg-turbo/libjpeg-turbo/releases/download/$LJT_VERSION/libjpeg-turbo-$LJT_VERSION.tar.gz"
        tar -xzf "$ROOT/build/ljt.tar.gz" -C "$ROOT/build"
    fi
    rm -rf "$BUILD/ljt-build"
    mkdir -p "$BUILD/ljt-build"
    (
        cd "$BUILD/ljt-build"
        cmake "$SRC" -G "Unix Makefiles" \
            -DCMAKE_SYSTEM_NAME=Linux -DCMAKE_SYSTEM_PROCESSOR="$CPU" \
            -DCMAKE_C_COMPILER="$BUILD/zig-cc" -DCMAKE_AR="$BUILD/zig-ar" -DCMAKE_RANLIB="$BUILD/zig-ranlib" \
            -DCMAKE_BUILD_TYPE=Release -DENABLE_SHARED=OFF -DENABLE_STATIC=ON -DWITH_TURBOJPEG=ON \
            -DREQUIRE_SIMD=OFF > cmake.log
        make -j"$(nproc)" turbojpeg-static > make.log   # the library only: no programs to link
    ) || { echo "libjpeg-turbo did not build: see $BUILD/ljt-build/*.log" >&2; exit 1; }
    grep "SIMD extensions" "$BUILD/ljt-build/cmake.log" || true
    mkdir -p "$LJT/lib" "$LJT/include"
    cp "$BUILD/ljt-build/libturbojpeg.a" "$LJT/lib/"
    cp "$SRC/src/turbojpeg.h" "$LJT/include/" 2>/dev/null || cp "$SRC/turbojpeg.h" "$LJT/include/"
fi

# ihcd
cd "$ROOT/box"
GIT="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
FULL="$VERSION+$GIT"
[ -z "$(git -C "$ROOT" status --porcelain -- box packaging VERSION 2>/dev/null)" ] || FULL="$FULL-dirty"
CGO_ENABLED=1 GOOS=linux GOARCH="$ARCH" GOTOOLCHAIN=local CC="$BUILD/zig-cc" \
    CGO_CFLAGS="-O2 -I$LJT/include" CGO_LDFLAGS="-L$LJT/lib -lturbojpeg" \
    go build -tags turbojpeg -trimpath \
    -ldflags "-s -w -X main.version=$FULL -linkmode external -extldflags -static" \
    -o "$BUILD/ihcd" ./cmd/ihcd

# the bundle
STAGE="$BUILD/stage"
rm -rf "$STAGE"
mkdir -p "$STAGE/bin"
cp "$BUILD/ihcd" "$STAGE/bin/"
cp "$ROOT/packaging/install.sh" "$ROOT/packaging/ihcd.service" "$ROOT/packaging/ihcd-gadget.service" \
   "$ROOT/packaging/99-ihc.rules" "$ROOT/box/THIRD_PARTY.md" "$STAGE/"
echo "$FULL" > "$STAGE/VERSION"
OUT="$ROOT/dist/ihc-box-$VERSION-linux-$ARCH.run"
sed -e "s/@VERSION@/$FULL/" -e "s/@ARCH@/$ARCH/" "$ROOT/packaging/run-header.sh" > "$OUT"
tar -C "$STAGE" -czf - . >> "$OUT"
chmod +x "$OUT"
echo "built $OUT ($(du -h "$OUT" | cut -f1), ihcd $FULL)"
