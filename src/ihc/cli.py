"""`ihc`: drive iphone-hid boxes from a shell or a test script.

    ihc discover                          # boxes on the local network
    ihc devices                           # their phones and states
    ihc tap 0.5 0.9                       # the only phone, or --device iphone-b40d9e
    ihc swipe 0.5 0.8 0.5 0.2
    ihc type "hello"
    ihc button home
    ihc screenshot shot.png
    ihc --url http://box-01.local:8000 status

Boxes: --url (repeatable), else every box found on the LAN. Token: --token, --token-file or
$IHC_TOKEN.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__
from .client import Farm, IhcError


def _token(args: argparse.Namespace) -> str | None:
    if args.token:
        return args.token.strip()
    if args.token_file:
        try:
            t = Path(args.token_file).read_text().strip()
        except OSError as e:
            raise SystemExit(f"ihc: cannot read the token file: {e}") from None
        if not t:
            raise SystemExit(f"ihc: the token file {args.token_file} is empty")
        return t
    return os.environ.get("IHC_TOKEN", "").strip() or None


def _farm(args: argparse.Namespace) -> Farm:
    if args.url:
        return Farm(*args.url, token=_token(args))
    return Farm.discover(token=_token(args))


def _phone(args: argparse.Namespace):
    farm = _farm(args)
    if args.device:
        return farm.device(args.device)
    phones = farm.devices()
    if len(phones) != 1:
        ids = ", ".join(p.id for p in phones) or "none"
        raise SystemExit(f"ihc: {len(phones)} phones ({ids}): choose one with --device")
    return phones[0]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ihc", description="Drive iphone-hid boxes.")
    ap.add_argument("--version", action="version", version=f"iphone-hid {__version__}")
    ap.add_argument("--url", action="append", help="a box's base URL, e.g. http://box-01.local:8000 (repeatable)")
    ap.add_argument("--token", help="API token (default $IHC_TOKEN)")
    ap.add_argument("--token-file", help="file holding the API token")
    ap.add_argument("--device", help="phone id (needed when there are several)")
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("discover", help="boxes announcing themselves on the local network")
    sub.add_parser("devices", help="phones and their states")
    sub.add_parser("status", help="full status of the phone, as JSON")
    p = sub.add_parser("tap", help="tap X Y (fractions of the screen)")
    p.add_argument("x", type=float)
    p.add_argument("y", type=float)
    p = sub.add_parser("long-press", help="touch and hold X Y")
    p.add_argument("x", type=float)
    p.add_argument("y", type=float)
    p.add_argument("--ms", type=int)
    for name, help_ in (("swipe", "swipe X1 Y1 X2 Y2"), ("drag", "drag and drop X1 Y1 X2 Y2")):
        p = sub.add_parser(name, help=help_)
        for c in ("x1", "y1", "x2", "y2"):
            p.add_argument(c, type=float)
        p.add_argument("--ms", type=int, help="duration of the move")
    p = sub.add_parser("scroll", help="scroll X Y LINES (positive: towards the top)")
    p.add_argument("x", type=float)
    p.add_argument("y", type=float)
    p.add_argument("lines", type=int)
    p = sub.add_parser("type", help="type TEXT")
    p.add_argument("text")
    p = sub.add_parser("key", help="press a key combination, e.g. cmd+space")
    p.add_argument("combo")
    p = sub.add_parser("button", help="press a phone button: home, app_switcher, spotlight, volume_up...")
    p.add_argument("name")
    p = sub.add_parser("open-url", help="open a URL through Search")
    p.add_argument("url")
    sub.add_parser("wake", help="wake a sleeping phone")
    p = sub.add_parser("screenshot", help="save the screen to FILE (.png or .jpg)")
    p.add_argument("file")
    args = ap.parse_args(argv)

    try:
        if args.command == "discover":
            from .discovery import discover

            urls = discover()
            print("\n".join(urls) if urls else "no box found (is zeroconf installed?)")
            return 0 if urls else 1
        if args.command == "devices":
            for ph in _farm(args).devices():
                print(f"{ph.id}\t{ph.info.get('state')}\t{ph.url}")
            return 0
        phone = _phone(args)
        c = args.command
        if c == "status":
            print(json.dumps(phone.status(), indent=2))
            return 0
        if c == "screenshot":
            fmt = "jpeg" if args.file.lower().endswith((".jpg", ".jpeg")) else "png"
            phone.screenshot(args.file, format=fmt)
            return 0
        result = {
            "tap": lambda: phone.tap(args.x, args.y),
            "long-press": lambda: phone.long_press(args.x, args.y, args.ms),
            "swipe": lambda: phone.swipe(args.x1, args.y1, args.x2, args.y2, args.ms),
            "drag": lambda: phone.drag(args.x1, args.y1, args.x2, args.y2, duration_ms=args.ms),
            "scroll": lambda: phone.scroll(args.x, args.y, args.lines),
            "type": lambda: phone.type(args.text),
            "key": lambda: phone.key(args.combo),
            "button": lambda: phone.button(args.name),
            "open-url": lambda: phone.open_url(args.url),
            "wake": lambda: phone.wake(),
        }[c]()
        print(f"{c}: done in {result.get('ms', 0):.0f} ms")
        return 0
    except IhcError as e:
        print(f"ihc: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
