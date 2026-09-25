#!/usr/bin/env python3
"""capture_check: check a video input (formats, real frame rate, JPEG passthrough, bandwidth).

    python tools/capture_check.py list
    python tools/capture_check.py probe --device /dev/video0 [--fourcc MJPG] [--size 1920x1080] [--fps 30] [--seconds 10]
    python tools/capture_check.py multi --device /dev/video0 --device /dev/video2 [--seconds 20]   # several cards at once
    python tools/capture_check.py snapshot --device /dev/video0 [--out docs/test-logs/snap.jpg]

A board's HDMI input (Orange Pi 5 Plus rk_hdmirx) is recognised by its name and read through a JPEG
encoder, as `ihc serve` does (`--hdmi` forces it, `--encoder mpp|gst|ffmpeg|builtin` picks the encoder,
`--keep-edid` leaves the input's EDID alone instead of advertising 1080p60).

No image analysis: it measures delivery only. Results go to a JSON-lines log in docs/test-logs/.
"""

from __future__ import annotations

import argparse
import os
import shutil
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ihc.jsonlog import EventLog  # noqa: E402
from ihc.registry import open_video_source  # noqa: E402
from ihc.video.capture import V4L2Capture, list_video_devices  # noqa: E402
from ihc.video.diagnose import diagnose  # noqa: E402
from ihc.video.pipe import is_hdmi_input  # noqa: E402
from ihc.video.v4l2 import describe_signal  # noqa: E402

LOG_DIR = Path(os.environ.get("IHC_TEST_LOG_DIR") or ROOT / "docs" / "test-logs")
# a new EDID makes the source replug: an HDMI input takes a few seconds to deliver its first frame
FIRST_FRAME_S = 15


def formats(device: str) -> str:
    if not shutil.which("v4l2-ctl"):
        return "(install v4l-utils for the format list)"
    r = subprocess.run(["v4l2-ctl", "-d", device, "--list-formats-ext"], capture_output=True, text=True, timeout=10)
    return (r.stdout or r.stderr).strip()


def dv_timings(device: str) -> str:
    """What the HDMI source sends right now (resolution, refresh), or why nothing."""
    if not shutil.which("v4l2-ctl"):
        try:
            return describe_signal(device)
        except OSError as e:
            return f"cannot open: {e}"
    r = subprocess.run(["v4l2-ctl", "-d", device, "--query-dv-timings"], capture_output=True, text=True, timeout=10)
    return (r.stdout or r.stderr).strip()


def device_name(device: str) -> str:
    return next((d["name"] for d in list_video_devices() if d["device"] == device or device in d["links"]), "")


def open_capture(device: str, fourcc: str, size: tuple[int, int], fps: int, *, hdmi: bool | None = None,
                 encoder: str = "auto", edid: str | None = "hdmi"):
    if hdmi is None:
        hdmi = is_hdmi_input(device_name(device))
    if hdmi:
        return open_video_source(device, size=size, fps=fps, hdmi=True, encoder=encoder, edid=edid)
    return V4L2Capture(device, size[0], size[1], fps, fourcc)


def probe(device: str, fourcc: str, size: tuple[int, int], fps: int, seconds: float, **source) -> dict:
    cap = open_capture(device, fourcc, size, fps, **source)
    try:
        f = cap.latest(timeout=FIRST_FRAME_S)
        t_end = time.monotonic() + seconds
        stamps, sizes, seq = [f.ts], [len(f.jpeg) if f.jpeg else 0], f.seq
        while time.monotonic() < t_end:
            try:
                f = cap.latest(newer_than=seq, timeout=2)
            except TimeoutError:
                break
            stamps.append(f.ts)
            sizes.append(len(f.jpeg) if f.jpeg else 0)
            seq = f.seq
        gaps = [b - a for a, b in zip(stamps, stamps[1:])]
        dur = stamps[-1] - stamps[0] if len(stamps) > 1 else 0
        stats = cap.stats()
        return {
            "device": device,
            "requested": f"{fourcc} {size[0]}x{size[1]}@{fps}",
            "delivered_size": list(f.size),
            "passthrough": f.jpeg is not None,
            "frames": len(stamps),
            "fps": round((len(stamps) - 1) / dur, 1) if dur else 0.0,
            "interval_ms_p50": round(statistics.median(gaps) * 1000, 1) if gaps else None,
            "interval_ms_p95": round(sorted(gaps)[int(0.95 * (len(gaps) - 1))] * 1000, 1) if gaps else None,
            "jpeg_kb_mean": round(statistics.fmean(sizes) / 1024, 1) if any(sizes) else None,
            "mbit_s": round(sum(sizes) * 8 / dur / 1e6, 1) if dur and any(sizes) else None,
            "dropped": stats["dropped"],
            "reopens": stats["reopens"],
            "status": stats["status"],
        }
    finally:
        cap.close()


def show(r: dict) -> None:
    print(f"{r['device']}: {r['requested']} -> {r['delivered_size'][0]}x{r['delivered_size'][1]}, {r['fps']} fps "
          f"(interval p50 {r['interval_ms_p50']} ms, p95 {r['interval_ms_p95']} ms), passthrough {r['passthrough']}, "
          f"{r['jpeg_kb_mean']} KB/frame = {r['mbit_s']} Mbit/s, dropped {r['dropped']}, reopens {r['reopens']}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["list", "probe", "multi", "snapshot"])
    ap.add_argument("--device", action="append", default=[])
    ap.add_argument("--fourcc", default="MJPG")
    ap.add_argument("--size", default="1920x1080")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--seconds", type=float, default=10)
    ap.add_argument("--out", help="snapshot file (default docs/test-logs/snap-<time>.jpg)")
    ap.add_argument("--log", help="JSON-lines log path")
    ap.add_argument("--hdmi", action="store_true", default=None, help="read the device as a board HDMI input")
    ap.add_argument("--encoder", default="auto", choices=["auto", "mpp", "gst", "ffmpeg", "builtin"],
                    help="HDMI input JPEG encoder (builtin: read the driver directly, nothing installed)")
    ap.add_argument("--keep-edid", action="store_true", help="HDMI input: do not advertise a 1080p60 EDID")
    args = ap.parse_args(argv)
    size = tuple(int(v) for v in args.size.lower().split("x"))
    source = {"hdmi": args.hdmi, "encoder": args.encoder, "edid": None if args.keep_edid else "hdmi"}

    if args.command == "list":
        devs = list_video_devices()
        if not devs:
            print("no /dev/video* devices")
        for d in devs:
            print(f"{d['device']}  {d['name']}")
            for link in d["links"]:
                print(f"    -> {link}")
            print("    " + formats(d["device"]).replace("\n", "\n    "))
            if is_hdmi_input(d["name"]):
                print("    HDMI input; the source sends:\n    " + dv_timings(d["device"]).replace("\n", "\n    "))
        if not any(is_hdmi_input(d["name"]) for d in devs):
            print("\nno HDMI input (hdmirx) among them. What this board says:")
            for line in diagnose():
                print("  " + line)
        return 0
    if not args.device:
        ap.error("--device is required")
    log = EventLog(args.log or LOG_DIR / f"capture-{time.strftime('%Y%m%d-%H%M%S')}.jsonl")
    try:
        if args.command == "probe":
            r = probe(args.device[0], args.fourcc, size, args.fps, args.seconds, **source)
            show(r)
            log("capture_probe", **r)
        elif args.command == "multi":
            results: dict[str, dict] = {}

            def run(dev: str) -> None:
                try:
                    results[dev] = probe(dev, args.fourcc, size, args.fps, args.seconds, **source)
                except Exception as e:  # report every card, whatever happens to one
                    results[dev] = {"device": dev, "error": str(e)}

            threads = [threading.Thread(target=run, args=(d,)) for d in args.device]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            for dev in args.device:
                r = results[dev]
                show(r) if "error" not in r else print(f"{dev}: ERROR {r['error']}")
                log("capture_multi", **r)
        else:
            cap = open_capture(args.device[0], args.fourcc, size, args.fps, **source)
            try:
                f = cap.latest(timeout=FIRST_FRAME_S)
            finally:
                cap.close()
            out = Path(args.out or LOG_DIR / f"snap-{time.strftime('%Y%m%d-%H%M%S')}.jpg")
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(f.to_jpeg(90))
            print(f"saved {out} ({f.width}x{f.height}, passthrough {f.jpeg is not None})")
            log("capture_snapshot", device=args.device[0], path=str(out), size=[f.width, f.height])
    except (OSError, TimeoutError) as e:
        print(f"error: {e}")
        return 1
    finally:
        log.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
