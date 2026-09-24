#!/usr/bin/env python3
"""Regenerate the README illustrations from the simulator: python tools/make_readme_images.py

Every picture is produced by the real code paths (pointer planning, calibration) running against the
deterministic simulator, so the numbers and paths shown are what the software actually does.
Needs matplotlib (not a runtime dependency).
"""

from __future__ import annotations

import json
import random
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from ihc.calibration import ClickCollector, calibrate  # noqa: E402
from ihc.input import keymap  # noqa: E402
from ihc.input.pointer import PointerCalibration, PointerModel  # noqa: E402
from ihc.sim.direct import direct_phone  # noqa: E402
from ihc.sim.pointer_style import draw_pointer  # noqa: E402
from ihc.sim.render import render_layout  # noqa: E402
from ihc.sim.rig import exact_calibration  # noqa: E402

OUT = ROOT / "docs" / "images"
PPT = 1.6  # pixels per point in the pictures
FONT = "DejaVuSans.ttf"
BOLD = "DejaVuSans-Bold.ttf"


def font(size: int, bold: bool = False):
    try:
        return ImageFont.truetype(BOLD if bold else FONT, size)
    except OSError:
        return ImageFont.load_default(size=size)


def screen(phone, pointer_pt=None) -> Image.Image:
    img = render_layout(phone.layout(), phone.model.width_pt, phone.model.height_pt, PPT, phone.top, phone.dark)
    if pointer_pt is not None:
        img = img.copy()
        draw_pointer(img, pointer_pt[0] * PPT - 0.5, pointer_pt[1] * PPT - 0.5, PPT)
    return Image.fromarray(img[:, :, ::-1])


def framed(img: Image.Image, label: str, pad: int = 14) -> Image.Image:
    """A phone-like bezel and a caption under it."""
    w, h = img.size
    out = Image.new("RGB", (w + 2 * pad, h + 2 * pad + 44), (255, 255, 255))
    d = ImageDraw.Draw(out)
    d.rounded_rectangle((0, 0, w + 2 * pad - 1, h + 2 * pad - 1), radius=58, fill=(22, 22, 24))
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=46, fill=255)
    out.paste(img, (pad, pad), mask)
    d.text((out.width / 2, h + 2 * pad + 22), label, font=font(20, True), fill=(40, 40, 45), anchor="mm")
    return out


def row(images: list[Image.Image], gap: int = 28, bg=(255, 255, 255)) -> Image.Image:
    w = sum(i.width for i in images) + gap * (len(images) + 1)
    h = max(i.height for i in images) + 2 * gap
    out = Image.new("RGB", (w, h), bg)
    x = gap
    for i in images:
        out.paste(i, (x, gap))
        x += i.width + gap
    return out


def type_text(chip, text):
    from ihc.hid import protocol as p

    for mods, keys in keymap.text_reports(text):
        chip.receive(p.kb_general(mods, keys))


def simulated_screens() -> None:
    from ihc.hid import protocol as p

    panels = []
    phone, chip, hid, clock = direct_phone()
    icon = phone.find("Notes")
    panels.append(framed(screen(phone, icon.center), "Home"))
    phone.open_app("Notes")
    clock.sleep(1)
    phone.editing, phone.editor_text = -1, ""
    type_text(chip, "Typed over a HID keyboard\nby iphone-hid")
    panels.append(framed(screen(phone, (300, 330)), "Notes"))
    phone.go_home()
    clock.sleep(1)
    chip.receive(p.kb_general(*keymap.parse_combo("cmd+space")))
    chip.receive(p.kb_general(0, []))
    type_text(chip, "set")
    panels.append(framed(screen(phone), "Spotlight (Cmd+Space)"))
    phone.open_app("Targets")
    clock.sleep(1)
    pm = PointerModel(hid, exact_calibration(chip.pointer), clock=clock, sleep=clock.sleep)
    rng = random.Random(3)
    for i in rng.sample(range(len(phone.targets)), 12):
        t = phone.targets[i]
        pm.move_to(t.x, t.y)
        pm.click()
    panels.append(framed(screen(phone, chip.pointer.current()), "Targets (accuracy test app)"))
    row(panels).save(OUT / "simulated-screens.png", optimize=True)


def label(d: ImageDraw.ImageDraw, xy, text: str, color, size: int = 21) -> None:
    """Bold text on a white rounded box, so it reads over any screen content."""
    f = font(size, True)
    box = d.multiline_textbbox(xy, text, font=f)
    d.rounded_rectangle((box[0] - 8, box[1] - 6, box[2] + 8, box[3] + 6), radius=8, fill=(255, 255, 255, 235),
                        outline=color, width=2)
    d.multiline_text(xy, text, font=f, fill=color)


def arrow(d: ImageDraw.ImageDraw, a, b, color, width=4, head=14):
    d.line((a, b), fill=color, width=width)
    v = np.array(b, float) - np.array(a, float)
    n = np.linalg.norm(v)
    if n < 1:
        return
    u = v / n
    left = np.array(b) - u * head + np.array([-u[1], u[0]]) * head * 0.55
    right = np.array(b) - u * head - np.array([-u[1], u[0]]) * head * 0.55
    d.polygon([tuple(b), tuple(left), tuple(right)], fill=color)


def pointer_modes() -> None:
    panels = []
    target = (170.0, 360.0)
    start = (300.0, 690.0)
    for absolute in (False, True):
        phone, chip, hid, clock = direct_phone()
        chip.pointer.absolute = absolute
        phone.open_app("Settings")
        clock.sleep(1)
        pm = PointerModel(hid, exact_calibration(chip.pointer), clock=clock, sleep=clock.sleep)
        pm.move_to(*start)
        chip.pointer.current()
        chip.pointer.history.clear()
        plan = pm.move_to(*target)
        img = screen(phone, chip.pointer.current())
        d = ImageDraw.Draw(img, "RGBA")
        s = lambda p: (p[0] * PPT, p[1] * PPT)  # noqa: E731
        if absolute:
            arrow(d, s(start), s(target), (10, 132, 255, 230), width=6, head=22)
            mid = ((start[0] + target[0]) / 2, (start[1] + target[1]) / 2)
            label(d, (s(mid)[0] - 60, s(mid)[1] + 40), "one absolute report,\nthen click", (10, 100, 230))
            title = "Absolute mode: 1 report"
        else:
            corner = pm.anchored_at(*plan.anchor)
            # positions after each paced report, once the pointer sits in the corner
            hist = [(x, y) for _, x, y in chip.pointer.history]
            first = next(i for i, p in enumerate(hist) if p == corner)
            runs = [corner] + hist[first + 1:]
            inset = 7  # draw along the screen edge a few pixels inside, so it stays visible
            v = lambda p: (max(p[0] * PPT, inset), max(p[1] * PPT, inset))  # noqa: E731
            arrow(d, s(start), v(corner), (255, 59, 48, 190), width=4, head=18)
            label(d, (s(start)[0] - 250, s(start)[1] - 250), "1  slam into the\n    nearest corner", (210, 40, 30))
            for a, b in zip(runs, runs[1:]):
                color = (10, 132, 255, 255) if b[0] != a[0] else (40, 160, 80, 255)
                d.line((v(a), v(b)), fill=color, width=7)
                d.ellipse((v(b)[0] - 5, v(b)[1] - 5, v(b)[0] + 5, v(b)[1] + 5), fill=color)
            label(d, (v((target[0], 0))[0] + 18, 118), "2  X run along the top:\n    coarse, then fine", (10, 100, 230))
            label(d, (s(target)[0] + 28, s((0, target[1] * 0.6))[1]), "3  Y run:\n    coarse,\n    then fine", (30, 140, 70))
            reports = sum(abs(c) + abs(f) for _, c, f in plan.runs)
            title = f"Relative mode: anchor + {reports} paced reports"
        r = 18
        d.ellipse((s(target)[0] - r, s(target)[1] - r, s(target)[0] + r, s(target)[1] + r), outline=(255, 149, 0), width=5)
        panels.append(framed(img, title))
    row(panels).save(OUT / "pointer-modes.png", optimize=True)


def calibration_page() -> dict:
    phone, chip, hid, clock = direct_phone()
    pm = PointerModel(hid, PointerCalibration(), clock=clock, sleep=clock.sleep)
    clicks = ClickCollector()
    phone.web_listeners.append(lambda url, ev: clicks.push(ev))
    phone.open_url("http://ihc-box.local:8000/calibrate/rig-1-1.2")
    all_clicks = []
    phone.web_listeners.append(lambda url, ev: all_clicks.append(ev) if ev["type"] == "click" else None)
    cal = calibrate(pm, clicks, click_timeout=0.05)
    phone.web_clicks.clear()
    img = screen(phone)
    d = ImageDraw.Draw(img, "RGBA")
    for ev in all_clicks:
        x, y = ev["x"] * PPT, (ev["y"] + phone.top) * PPT
        d.ellipse((x - 3, y - 3, x + 3, y + 3), fill=(255, 59, 48, 170))
    v = cal.extra["validation_error_pt"]
    caption = f"{len(all_clicks)} clicks, validation error max {v['max']} pt"
    row([framed(img, "Calibration page in Safari")]).save(OUT / "calibration-page.png", optimize=True)
    return {"clicks": len(all_clicks), "validation": v, "caption": caption}


def accuracy() -> dict:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    configs = [("Absolute", True, 1.0), ("Relative\nTracking 0.4", False, 0.4),
               ("Relative\nTracking 1.0", False, 1.0), ("Relative\nTracking 2.5", False, 2.5)]
    results = {}
    for label, absolute, tracking in configs:
        phone, chip, hid, clock = direct_phone()
        chip.pointer.absolute = absolute
        chip.pointer.tracking = tracking
        pm = PointerModel(hid, PointerCalibration(), clock=clock, sleep=clock.sleep)
        clicks = ClickCollector()
        phone.web_listeners.append(lambda url, ev: clicks.push(ev))
        phone.open_url("http://ihc-box.local:8000/calibrate/x")
        t0 = clock()
        cal = calibrate(pm, clicks, click_timeout=0.05)
        cal_s = clock() - t0
        phone.open_app("Targets")
        clock.sleep(1)
        rng = random.Random(0)
        errs, secs = [], []
        for _ in range(200):
            t = phone.targets[rng.randrange(len(phone.targets))]
            t1 = clock()
            pm.move_to(t.x, t.y)
            pm.click()
            secs.append(clock() - t1)
            errs.append(phone.tap_log[-1]["error_pt"])
        results[label] = {"errors": errs, "seconds": secs, "mode": cal.mode, "calibration_s": round(cal_s, 1)}

    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12, 4.2), dpi=120)
    labels = list(results)
    a1.boxplot([results[k]["errors"] for k in labels], tick_labels=labels, showfliers=True, widths=0.5)
    a1.axhline(4.0, color="#ff3b30", linestyle="--", linewidth=1.2)
    a1.text(0.55, 4.08, "target: 95% within 4 pt", color="#ff3b30", fontsize=9)
    a1.set_ylabel("tap error (points)")
    a1.set_ylim(0, 4.6)
    a1.set_title("Where 200 random taps landed")
    means = [statistics.median(results[k]["seconds"]) for k in labels]
    bars = a2.bar(labels, means, color=["#0a84ff", "#34c759", "#34c759", "#34c759"], width=0.5)
    a2.axhline(1.5, color="#ff3b30", linestyle="--", linewidth=1.2)
    a2.text(-0.25, 1.53, "target: < 1.5 s", color="#ff3b30", fontsize=9)
    for b, m in zip(bars, means):
        a2.text(b.get_x() + b.get_width() / 2, m + 0.03, f"{m:.2f} s", ha="center", fontsize=9)
    a2.set_ylabel("seconds per tap (median)")
    a2.set_ylim(0, 1.8)
    a2.set_title("Time per tap, including the click")
    for ax in (a1, a2):
        ax.spines[["top", "right"]].set_visible(False)
    fig.suptitle("Simulated iPhone 15, pointer calibrated through the Safari page (no computer vision)", fontsize=11)
    fig.tight_layout()
    fig.savefig(OUT / "accuracy.png")
    summary = {k: {"max_error_pt": round(max(v["errors"]), 2), "p95_error_pt": round(sorted(v["errors"])[189], 2),
                   "median_s": round(statistics.median(v["seconds"]), 2), "calibration_s": v["calibration_s"],
                   "mode": v["mode"]} for k, v in results.items()}
    return summary


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    simulated_screens()
    pointer_modes()
    info = {"calibration": calibration_page(), "accuracy": accuracy()}
    (OUT / "figures.json").write_text(json.dumps(info, indent=2) + "\n")
    print(json.dumps(info, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
