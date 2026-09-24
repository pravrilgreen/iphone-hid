"""`ihc` command line: serve a farm (real or simulated), discover rigs, list devices.

    ihc serve --auto                       # zero-config: every rig plugged into this host
    ihc serve --sim 4                      # 4 simulated iPhones, console at http://<LAN IP>:8000
    ihc serve --config farm.toml --log logs/farm.jsonl
    ihc discover > farm.toml               # pair serial ports and capture cards by USB hub
    ihc devices                            # every box on the LAN (mDNS), else this host
    ihc devices --url http://farm-01:8000
"""

from __future__ import annotations

import argparse
import socket
import sys


def lan_ip() -> str:
    """The primary LAN address: the source address of a route to a public IP (a UDP socket
    connect sends no packet)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .api import create_app
    from .jsonlog import EventLog
    from .registry import load_config, simulated

    log = EventLog(args.log) if args.log else None
    if args.config:
        registry = load_config(args.config, log=log)
    elif args.auto:
        from .registry import auto

        registry = auto(log=log, state_dir=args.state_dir)
    else:
        registry = simulated(args.sim, model=args.model, calibrated=not args.uncalibrated, log=log,
                             absolute=args.sim_absolute, bridge=args.sim_bridge)
    public_url = (args.public_url or f"http://{lan_ip()}:{args.port}").rstrip("/")
    announcement = None
    try:
        if not args.no_monitor:
            registry.start_monitors()
        if hasattr(registry, "start_rescan"):  # zero-config: pick up rigs plugged in later
            registry.start_rescan(on_added=None if args.no_monitor else lambda d: d.start_monitor())
        app = create_app(registry, public_url=public_url, log=log)
        ids = ", ".join(d.id for d in registry.devices()) or "none yet"
        print(f"ihc: {len(registry.devices())} device(s): {ids}", file=sys.stderr)
        print(f"ihc: console at {public_url}/, API reference at {public_url}/docs", file=sys.stderr)
        announcement = _advertise(args.port, public_url, len(registry.devices()), log)
        config = uvicorn.Config(
            app, host=args.host, port=args.port, log_level=args.log_level,
            ws_per_message_deflate=False,  # JPEG frames do not compress: deflate would only burn CPU
            timeout_graceful_shutdown=3,  # live streams never end on their own
        )
        try:
            uvicorn.Server(config).run()
        except KeyboardInterrupt:  # uvicorn re-raises Ctrl+C once it has shut down gracefully
            pass
    finally:
        if announcement is not None:
            announcement.close()
        registry.close()
        if log is not None:
            log.close()
    return 0


def _advertise(port: int, public_url: str, devices: int, log):
    """Announce this box over mDNS when ihc.discovery is available; None otherwise."""
    try:
        from .discovery import advertise
    except ImportError:
        return None
    from urllib.parse import urlparse

    try:
        return advertise(port, devices=devices, address=urlparse(public_url).hostname, log=log)
    except Exception as e:  # mDNS is a convenience: never keep the server from starting
        print(f"ihc: mDNS announcement failed: {e}", file=sys.stderr)
        return None


def cmd_discover(args: argparse.Namespace) -> int:
    """Print a farm.toml (valid TOML; the pairing summary goes into # comments)."""
    from .registry import config_from_discovery, discover

    rigs = discover(args.serial_dir, args.video_dir)
    print(f"# ihc discover: {len(rigs)} rig(s) found (serial port + capture card under the same USB hub)")
    for i, r in enumerate(rigs, 1):
        print(f"#   iphone-{i:02d}: hub {r.usb_path}")
        print(f"#     serial {r.serial}")
        print(f"#     video  {r.video}")
    print()
    if rigs:
        print(config_from_discovery(rigs, args.model))
        return 0
    print("# Template (fill in by hand):")
    print("# [[device]]")
    print('# id = "iphone-01"')
    print(f'# model = "{args.model or "iphone-15"}"')
    print('# hid = { port = "/dev/serial/by-path/...-port0", baud = 9600 }')
    print('# video = { device = "/dev/v4l/by-path/...-video-index0", size = "1920x1080", fps = 30 }')
    print('# calibration = "calib/iphone-01.json"')
    print("ihc discover: no rig found. Each phone's USB-serial cable and capture card must hang off one USB hub; "
          "otherwise write farm.toml by hand from `ls -l /dev/serial/by-path /dev/v4l/by-path`.", file=sys.stderr)
    return 1


def cmd_devices(args: argparse.Namespace) -> int:
    from .client import Farm, IhcError

    try:
        with Farm(*args.url, timeout=args.timeout) as farm:
            phones = farm.devices()
    except IhcError as e:
        print(f"ihc: {e}", file=sys.stderr)
        return 1
    rows = [("ID", "MODEL", "STATE", "POINTER", "CALIBRATION", "HOST")]
    for d in phones:
        st = d.info
        cal = st.get("calibration") or {}
        val = cal.get("validation") or {}
        cal_text = cal.get("method", "?") + (f" ({val['mean']:.1f} pt)" if val.get("mean") is not None else "")
        rows.append((d.id, d.model, st.get("state", "?"), (st.get("pointer") or {}).get("mode", "relative"),
                     cal_text, d.host))
    widths = [max(len(str(r[i])) for r in rows) for i in range(len(rows[0]))]
    for r in rows:
        print("  ".join(str(v).ljust(w) for v, w in zip(r, widths)).rstrip())
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="ihc", description="iPhone control over HDMI capture + HID hardware")
    sub = ap.add_subparsers(dest="command", required=True)

    s = sub.add_parser("serve", help="run the API and web console")
    src = s.add_mutually_exclusive_group(required=True)
    src.add_argument("--auto", action="store_true", help="zero-config: find and probe every rig on this host")
    src.add_argument("--config", metavar="FARM.TOML", help="hardware devices from a farm config")
    src.add_argument("--sim", type=int, metavar="N", help="N simulated iPhones")
    s.add_argument("--model", default="iphone-15", help="simulated model (default iphone-15)")
    s.add_argument("--uncalibrated", action="store_true", help="simulated phones start uncalibrated")
    s.add_argument("--sim-absolute", action="store_true", help="simulated phones follow absolute pointer reports")
    s.add_argument("--sim-bridge", action="store_true", help="simulate the ESP32 bridge instead of a CH9329")
    s.add_argument("--state-dir", metavar="PATH", default=None,
                   help="with --auto: where calibration files live (default $IHC_STATE_DIR or ~/.local/share/ihc)")
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--public-url", help="URL phones use to reach this server (default http://<LAN IP>:<port>)")
    s.add_argument("--log", metavar="PATH", help="JSON-lines event log")
    s.add_argument("--no-monitor", action="store_true", help="do not start the health monitors")
    s.add_argument("--log-level", default="info", choices=["critical", "error", "warning", "info", "debug"])
    s.set_defaults(fn=cmd_serve)

    d = sub.add_parser("discover", help="find rigs and print a starter farm.toml")
    d.add_argument("--model", default="", help="model key to write into the config")
    d.add_argument("--serial-dir", default="/dev/serial/by-path")
    d.add_argument("--video-dir", default="/dev/v4l/by-path")
    d.set_defaults(fn=cmd_discover)

    v = sub.add_parser("devices", help="list the devices of one or more hosts")
    v.add_argument("--url", action="append", default=None,
                   help="host URL (repeatable; default: every box found on the LAN, else http://127.0.0.1:8000)")
    v.add_argument("--timeout", type=float, default=10.0)
    v.set_defaults(fn=cmd_devices)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "sim", None) is not None and args.sim < 1:
        print("ihc: --sim needs at least 1 device", file=sys.stderr)
        return 2
    if args.command == "devices" and not args.url:
        from .discovery import discover

        args.url = discover(1.5) or ["http://127.0.0.1:8000"]
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
