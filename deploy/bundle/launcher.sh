#!/bin/sh
# Launcher of the self-contained box bundle: runs an iphone-hid entry point with the bundle's own
# Python and libraries (nothing from the system Python). bin/ihc is this file; bin/ihc-hidtest and
# bin/ihc-capture-check link to it, and the name it is called by picks the entry point.
set -eu
HERE="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
export PYTHONPATH="$HERE/app:$HERE/lib"
export PYTHONNOUSERSITE=1
export PYTHONDONTWRITEBYTECODE=1
# tool logs: in the caller's home, not inside the (root-owned) install
export IHC_TEST_LOG_DIR="${IHC_TEST_LOG_DIR:-${HOME:-/tmp}/ihc-test-logs}"

run() {
    if [ -n "${IHC_PYTHON:-}" ]; then
        exec $IHC_PYTHON "$@"  # (an interpreter command line, e.g. under an emulator for testing)
    fi
    exec "$HERE/python/bin/python3" "$@"
}

case "$(basename "$0")" in
    ihc-hidtest) run "$HERE/app/tools/hidtest.py" "$@" ;;
    ihc-capture-check) run "$HERE/app/tools/capture_check.py" "$@" ;;
    *) run -m ihc.cli "$@" ;;
esac
