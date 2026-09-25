"""`ihc gadget` (also tools/gadget.py): make this board the phone's USB keyboard and mouse.

    sudo ihc gadget up              # keyboard + media keys + relative and absolute mouse
    sudo ihc gadget up --profile A  # absolute pointer only (no relative mouse)
    ihc gadget status               # is a USB device controller there, did the phone enumerate?
    sudo ihc gadget down

Then check it with the same commands as a CH9329: `ihc-hidtest --gadget "info; move 100 0"`
(from a source checkout: `python3 tools/hidtest.py --gadget ...`).

Needs a USB port that can act as a device (on the Orange Pi 5 Plus: the Type-C port next to the
USB 3 ports, not the power port). Cable it to a USB-A port of the phone's hub with a USB-A to
USB-C cable. Profiles: RA (default), A (absolute only), R (relative only), K (keyboard only).
"""

from __future__ import annotations

import argparse
import json
import os
import pwd
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .hid import gadget as g


def _need_root() -> None:
    if os.geteuid() != 0:
        print("error: run this with sudo (configfs and /dev/hidg* belong to root)", file=sys.stderr)
        raise SystemExit(2)


def _load_framework(configfs: str) -> None:
    """libcomposite provides /sys/kernel/config/usb_gadget; configfs itself may need mounting."""
    if not Path(configfs).is_dir() and shutil.which("modprobe"):
        subprocess.run(["modprobe", "libcomposite"], check=False)
    cfg_root = Path(configfs).parent
    if cfg_root.is_dir() and not any(cfg_root.iterdir()):
        subprocess.run(["mount", "-t", "configfs", "none", str(cfg_root)], check=False)


def _give_nodes(nodes: dict[str, str], owner: str | None) -> None:
    if not owner:
        return
    pw = pwd.getpwnam(owner)
    for path in nodes.values():
        os.chown(path, pw.pw_uid, pw.pw_gid)
        os.chmod(path, 0o660)


def _print_status(st: dict) -> None:
    print(f"USB device controllers: {', '.join(st['udcs']) or 'NONE (the USB-C port is in host mode)'}")
    for udc, user in st["udc_users"].items():
        if user != st["name"]:
            print(f"  {udc} is used by another gadget: {user!r}")
    if not st["exists"]:
        print(f"gadget {st['name']!r}: not set up (sudo ihc gadget up, or sudo python3 tools/gadget.py up)")
        return
    connected = "phone connected" if st["state"] == "configured" else "phone NOT connected"
    print(f"gadget {st['name']!r}: bound to {st['udc'] or 'nothing'} | USB state {st['state']} ({connected})")
    for fn in st["functions"]:
        print(f"  {fn:<9} {st['nodes'].get(fn, '(no node yet)')}")


def add_arguments(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--name", default=g.DEFAULT_NAME, help="configfs gadget name (default ihc)")
    ap.add_argument("--configfs", default=g.CONFIGFS, help=argparse.SUPPRESS)
    sub = ap.add_subparsers(dest="cmd", required=True)
    up = sub.add_parser("up", help="create the gadget and bind it to the USB device controller")
    up.add_argument("--profile", default="RA", choices=list(g.PROFILES),
                    help="RA: keyboard, media keys, relative mouse and absolute pointer (default); "
                         "A: absolute only; R: relative only; K: keyboard only")
    up.add_argument("--udc", help="USB device controller (default: the only one)")
    up.add_argument("--owner", default=os.environ.get("SUDO_USER"),
                    help="user who gets the /dev/hidg* nodes (default: the one who ran sudo)")
    up.add_argument("--pid", type=lambda s: int(s, 0), default=g.COMPOSITE_GADGET_PID, help="USB product id")
    up.add_argument("--replace", action="store_true", help="tear down an existing gadget of this name first")
    sub.add_parser("down", help="unbind and remove the gadget")
    st = sub.add_parser("status", help="controllers, binding, USB state, device nodes")
    st.add_argument("--json", action="store_true")


def run(args: argparse.Namespace) -> int:
    if args.cmd == "status":
        status = g.gadget_status(args.name, args.configfs)
        if args.json:
            print(json.dumps(status, indent=2))
        else:
            _print_status(status)
        return 0

    _need_root()
    if args.cmd == "down":
        print("removed" if g.gadget_down(args.name, configfs=args.configfs) else f"no gadget {args.name!r}")
        return 0

    _load_framework(args.configfs)
    if args.replace:
        g.gadget_down(args.name, configfs=args.configfs)
    try:
        udc = g.gadget_up(args.name, args.profile, udc=args.udc, configfs=args.configfs, product=args.pid)
    except g.GadgetError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    want = set(g.PROFILES[args.profile])
    deadline = time.monotonic() + 3
    nodes = {}
    while time.monotonic() < deadline:
        nodes = g.gadget_nodes(args.name, args.configfs)
        if set(nodes) == want and all(os.path.exists(n) for n in nodes.values()):
            break
        time.sleep(0.1)
    _give_nodes(nodes, args.owner)
    print(f"gadget {args.name!r} ({args.profile}) bound to {udc}")
    _print_status(g.gadget_status(args.name, args.configfs))
    print("next: plug the USB-C port into a USB-A port of the phone's hub, unlock the phone, then "
          "`ihc-hidtest --gadget \"info; move 100 0; click\"` (from a checkout: python3 tools/hidtest.py --gadget ...)")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gadget", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_arguments(ap)
    return run(ap.parse_args(argv))
