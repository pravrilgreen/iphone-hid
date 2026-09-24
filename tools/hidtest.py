#!/usr/bin/env python3
"""hidtest: interactive checker for a CH9329 (or the ESP32 bridge) during the phase 0 hardware tests.

    python tools/hidtest.py --port /dev/ttyUSB0             # shell; type `help`
    python tools/hidtest.py --port /dev/ttyUSB0 info        # run one command and exit
    python tools/hidtest.py --port /dev/ttyUSB0 "move 200 0; click"
    python tools/hidtest.py --fake                          # dry run against the simulated chip

Each session appends a JSON-lines log (default docs/test-logs/hidtest-<time>.jsonl) holding every
command, every frame sent and received and the operator's answers. Send that file back.

Moves are in HID units (the -127..127 steps a mouse reports), not pixels: nothing is calibrated yet.
"""

from __future__ import annotations

import argparse
import cmd
import json
import platform
import shlex
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))  # allow running from a checkout without `pip install -e .`

from ihc.hid import protocol as p  # noqa: E402
from ihc.hid.base import HidError, HidTimeout  # noqa: E402
from ihc.hid.ch9329 import CH9329Backend  # noqa: E402
from ihc.hid.config import ChipConfig, parse_value  # noqa: E402
from ihc.hid.scan import candidate_ports, list_serial_ports  # noqa: E402
from ihc.input import keymap  # noqa: E402
from ihc.jsonlog import EventLog  # noqa: E402

LOG_DIR = ROOT / "docs" / "test-logs"
BUTTONS = {"left": p.MOUSE_LEFT, "right": p.MOUSE_RIGHT, "middle": p.MOUSE_MIDDLE}
REQUIRED = object()
# Frames `raw` refuses to send: they write chip flash. Use the `cfg` commands instead.
FLASH_WRITES = {p.Cmd.SET_PARA_CFG, p.Cmd.SET_USB_STRING, p.Cmd.SET_DEFAULT_CFG}


def _int(s: str) -> int:
    return int(s, 0)


def _bool(s: str) -> bool:
    if s.lower() in ("1", "y", "yes", "true", "on"):
        return True
    if s.lower() in ("0", "n", "no", "false", "off"):
        return False
    raise ValueError(f"expected yes/no, got {s!r}")


def parse_args(arg: str, spec: list[tuple[str, type, object]]) -> dict:
    """Positional tokens fill `spec` in order; `name=value` sets any entry by name."""
    types = {name: typ for name, typ, _ in spec}
    out: dict = {}
    positional = []
    for tok in shlex.split(arg):
        name, eq, value = tok.partition("=")
        if eq and name in types:
            out[name] = types[name](value)
        elif eq:
            raise ValueError(f"unknown option {name!r}; options: {', '.join(types)}")
        else:
            positional.append(tok)
    if len(positional) > len(spec):
        raise ValueError(f"too many arguments; expected: {' '.join(n for n, _, _ in spec)}")
    for (name, typ, _), tok in zip(spec, positional):
        if name in out:
            raise ValueError(f"{name} given twice")
        out[name] = typ(tok)
    for name, _, default in spec:
        if name not in out:
            if default is REQUIRED:
                raise ValueError(f"missing argument {name!r}")
            out[name] = default
    return out


def _unquote(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1]
    return text.replace("\\n", "\n").replace("\\t", "\t")


def _stats_ms(samples: list[float]) -> str:
    if not samples:
        return "no samples"
    ms = sorted(s * 1000 for s in samples)
    p95 = ms[min(len(ms) - 1, int(round(0.95 * (len(ms) - 1))))]
    return f"mean {statistics.fmean(ms):.1f} ms, p50 {statistics.median(ms):.1f}, p95 {p95:.1f}, max {ms[-1]:.1f} (n={len(ms)})"


class HidShell(cmd.Cmd):
    prompt = "hid> "

    def __init__(self, hid: CH9329Backend, log: EventLog, *, ask=input, fake: bool = False):
        super().__init__()
        self.hid = hid
        self.log = log
        self._ask = ask
        self.fake = fake
        self.buttons = 0  # mouse buttons held by `down`
        self.failed = False
        self.settings = {"step": 10, "interval": 0.02, "key_hold": 0.02, "key_gap": 0.02}
        self._sim_seen = 0
        self._sim_pos = None
        self._sim_text = ""

    # -- shell plumbing ------------------------------------------------------------------------

    def ask(self, question: str) -> str:
        try:
            answer = self._ask(question)
        except EOFError:
            answer = ""
        self.log("answer", question=question, answer=answer)
        return answer

    def onecmd(self, line: str) -> bool:
        line = line.strip()
        if not line or line.startswith("#"):
            return False
        self.log("cmd", line=line)
        try:
            return bool(super().onecmd(line))
        except KeyboardInterrupt:
            self.hid.release_all()
            self.buttons = 0
            print("interrupted; released all keys and buttons")
            self.log("interrupted", line=line)
        except HidError as e:
            self.failed = True
            print(f"error: {e}")
            # a command may have failed half-way through a click, drag or key: never leave it held
            try:
                self.hid.release_all()
                self.buttons = 0
                print("released all keys and buttons")
            except HidError:
                print("could not release keys/buttons: check the cable, then `release`")
        except (ValueError, OSError) as e:
            self.failed = True
            print(f"error: {e}")
            self.log("error", line=line, kind=type(e).__name__, error=str(e))
        return False

    def postcmd(self, stop: bool, line: str) -> bool:
        if self.fake:
            self._print_sim_changes()
        return stop

    def emptyline(self) -> bool:
        return False

    def default(self, line: str) -> None:
        raise ValueError(f"unknown command {line.split()[0]!r}; type `help`")

    def run_script(self, text: str) -> None:
        for part in text.split(";"):
            if self.onecmd(part):
                break
            self.postcmd(False, part)

    # -- chip ----------------------------------------------------------------------------------

    def do_info(self, arg: str) -> None:
        """info: chip version, whether its USB side is enumerated, keyboard LEDs (GET_INFO)."""
        i = self.hid.info()
        usb = "connected" if i["usb_connected"] else "NOT connected"
        leds = ", ".join(f"{k.replace('_', ' ')} {'on' if i[k] else 'off'}" for k in ("num_lock", "caps_lock", "scroll_lock"))
        print(f"chip {i['version']} ({i['version_raw']:#04x}) | USB {usb} (status {i['usb_status']:#04x}) | {leds}")
        self.log("info", **i)

    def do_watch(self, arg: str) -> None:
        """watch [interval=0.5] [duration=0]: poll GET_INFO and print every change, until Ctrl+C
        (or `duration` seconds). Use it while locking/unlocking the iPhone or answering popups."""
        a = parse_args(arg, [("interval", float, 0.5), ("duration", float, 0.0)])
        print("watching; Ctrl+C to stop")
        t0, prev = time.monotonic(), None
        try:
            while not a["duration"] or time.monotonic() - t0 < a["duration"]:
                try:
                    i = self.hid.info()
                    state = f"USB {'connected' if i['usb_connected'] else 'NOT connected'}, caps lock {'on' if i['caps_lock'] else 'off'}"
                except HidTimeout:
                    state = "no reply from chip"
                if state != prev:
                    print(f"[{time.monotonic() - t0:7.1f} s] {state}")
                    self.log("watch_change", elapsed=round(time.monotonic() - t0, 2), state=state)
                    prev = state
                time.sleep(a["interval"])
        except KeyboardInterrupt:
            print("watch stopped")

    def do_cfg(self, arg: str) -> None:
        """cfg [show]            print the 50-byte parameter block (read only)
        cfg save [FILE]          back it up to JSON (default docs/test-logs/ch9329-cfg-<time>.json)
        cfg set FIELD=VALUE ...  change fields, e.g. `cfg set work_mode=1` (asks for YES, backs up first)
        cfg restore FILE         write a backup back (asks for YES)
        cfg default              factory defaults: 9600 baud, address 0 (asks for YES)
        Writes take effect at the next power-up: unplug and replug the HID end of the cable."""
        sub, _, rest = arg.strip().partition(" ")
        handlers = {"": self._cfg_show, "show": self._cfg_show, "save": self._cfg_save_cmd, "set": self._cfg_set,
                    "restore": self._cfg_restore, "default": self._cfg_default}
        if sub not in handlers:
            raise ValueError(f"unknown cfg subcommand {sub!r}; see `help cfg`")
        handlers[sub](rest.strip())

    def _cfg_show(self, rest: str) -> None:
        cfg = self.hid.get_config()
        print("\n".join(cfg.describe()))
        print(f"raw: {cfg.to_bytes().hex(' ')}")
        for w in cfg.warnings():
            print(f"WARNING: {w}")
        self.log("config", **cfg.to_dict(), warnings=cfg.warnings())

    def _cfg_save(self, path: str | None, cfg: ChipConfig) -> Path:
        if path:
            out = Path(path)
            if out.exists():
                raise ValueError(f"{out} already exists; pick another name")
        else:
            stem = LOG_DIR / f"ch9329-cfg-{time.strftime('%Y%m%d-%H%M%S')}"
            out, n = stem.with_suffix(".json"), 1
            while out.exists():
                n += 1
                out = stem.with_name(f"{stem.name}-{n}.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({**cfg.to_dict(), "port": self.hid.port, "saved_at": time.strftime("%Y-%m-%d %H:%M:%S")}, indent=2) + "\n")
        self.log("config_saved", path=str(out))
        return out

    def _cfg_save_cmd(self, rest: str) -> None:
        print(f"saved to {self._cfg_save(rest or None, self.hid.get_config())}")

    def _cfg_set(self, rest: str) -> None:
        changes = {}
        for tok in shlex.split(rest):
            name, eq, value = tok.partition("=")
            if not eq:
                raise ValueError("use FIELD=VALUE, e.g. `cfg set work_mode=1`")
            changes[name] = parse_value(name, value)
        if not changes:
            raise ValueError("nothing to set; see `help cfg` and the field names printed by `cfg`")
        cur = self.hid.get_config()
        self._cfg_write(cur, cur.replace(**changes))

    def _cfg_restore(self, rest: str) -> None:
        if not rest:
            raise ValueError("usage: cfg restore FILE")
        new = ChipConfig.from_dict(json.loads(Path(rest).read_text()))
        self._cfg_write(self.hid.get_config(), new)

    def _cfg_write(self, cur: ChipConfig, new: ChipConfig) -> None:
        new, notes = new.for_write()
        new.validate_for_write()
        changes = cur.diff(new)
        if not changes:
            print("nothing to change")
            return
        print("changes:")
        for name, old, value in changes:
            fmt = (lambda v: v.hex(" ")) if isinstance(old, bytes) else (lambda v: f"{v} ({v:#x})")
            print(f"  {name}: {fmt(old)} -> {fmt(value)}")
        for n in notes:
            print(f"  note: {n}")
        backup = self._cfg_save(None, cur)
        print(f"current block backed up to {backup}")
        print("the chip applies new values at its next power-up (unplug and replug the HID end).")
        names = {c[0] for c in changes}
        if "baud" in names:
            print(f"after that, connect with --baud {new.get('baud')}; if the link is lost run `python -m ihc.hid.scan`.")
        if "address" in names:
            print(f"after that, connect with --addr {new.get('address'):#04x}.")
        if self.ask("type YES to write: ").strip() != "YES":
            print("not written")
            self.log("config_write_cancelled")
            return
        self.hid.set_config(new)
        readback = self.hid.get_config()
        same = readback == new
        print("written; read back " + ("matches" if same else f"differs in {[c[0] for c in new.diff(readback)]}"))
        print(f"undo: python tools/hidtest.py --port {self.hid.port} [--baud N] cfg restore {backup}")
        self.log("config_written", before=cur.to_dict(), after=new.to_dict(), readback=readback.to_dict(), backup=str(backup))

    def _cfg_default(self, rest: str) -> None:
        cur = self.hid.get_config()
        backup = self._cfg_save(None, cur)
        print(f"current block backed up to {backup}")
        print("this restores factory parameters and USB strings (9600 baud, address 0), effective at next power-up")
        if self.ask("type YES to restore factory defaults: ").strip() != "YES":
            print("not written")
            return
        self.hid.set_default_config()
        print("done; after unplug/replug connect with --baud 9600")
        self.log("config_default", before=cur.to_dict(), backup=str(backup))

    def do_reset(self, arg: str) -> None:
        """reset: software-reset the chip; the iPhone sees the accessory disconnect and reconnect."""
        self.hid.reset()
        self.buttons = 0
        print("reset sent")

    def do_usbstr(self, arg: str) -> None:
        """usbstr: read the vendor/product/serial USB string settings."""
        for kind, name in ((0, "vendor"), (1, "product"), (2, "serial")):
            s = self.hid.get_usb_string(kind)
            print(f"{name:8} {s!r}")
            self.log("usb_string", kind=name, value=s)

    # -- pointer -------------------------------------------------------------------------------

    def _rel(self, dx: int = 0, dy: int = 0, *, buttons: int | None = None, wheel: int = 0) -> None:
        self.hid.mouse_rel(dx, dy, self.buttons if buttons is None else buttons, wheel)

    def _move(self, dx: int, dy: int, step: int, interval: float) -> int:
        """Open-loop move: X first, then Y, `step` units per report, one report per `interval`."""
        if not 1 <= step <= 127:
            raise ValueError("step must be 1..127")
        reports = 0
        for axis, total in ((0, dx), (1, dy)):
            while total:
                d = max(-step, min(step, total))
                self._rel(d, 0) if axis == 0 else self._rel(0, d)
                total -= d
                reports += 1
                time.sleep(interval)
        return reports

    def _click(self, bit: int, hold: float) -> None:
        self._rel(buttons=self.buttons | bit)
        time.sleep(hold)
        self._rel()

    def _move_spec(self) -> list:
        return [("step", _int, self.settings["step"]), ("interval", float, self.settings["interval"])]

    def do_move(self, arg: str) -> None:
        """move DX DY [step=10] [interval=0.02]: relative move in HID units (+x right, +y down)."""
        a = parse_args(arg, [("dx", _int, REQUIRED), ("dy", _int, REQUIRED), *self._move_spec()])
        n = self._move(a["dx"], a["dy"], a["step"], a["interval"])
        print(f"moved ({a['dx']}, {a['dy']}) in {n} reports")

    def do_click(self, arg: str) -> None:
        """click [left|right|middle] [hold=0.08]: press and release a mouse button."""
        a = parse_args(arg, [("button", str, "left"), ("hold", float, 0.08)])
        self._click(BUTTONS[a["button"]], a["hold"])

    def do_home(self, arg: str) -> None:
        """home: right click, which AssistiveTouch must map to Home (docs/iphone-setup.md)."""
        self._click(p.MOUSE_RIGHT, 0.08)

    def do_switcher(self, arg: str) -> None:
        """switcher: middle click, which AssistiveTouch must map to App Switcher."""
        self._click(p.MOUSE_MIDDLE, 0.08)

    def do_down(self, arg: str) -> None:
        """down [left|right|middle]: press and keep holding a button (drag by hand with `move`)."""
        self.buttons |= BUTTONS[parse_args(arg, [("button", str, "left")])["button"]]
        self._rel()

    def do_up(self, arg: str) -> None:
        """up [left|right|middle|all]: release held buttons."""
        name = parse_args(arg, [("button", str, "all")])["button"]
        self.buttons &= 0 if name == "all" else ~BUTTONS[name]
        self._rel()

    def do_corner(self, arg: str) -> None:
        """corner [reports=30] [interval=0.01]: push the pointer into the top-left corner."""
        a = parse_args(arg, [("reports", _int, 30), ("interval", float, 0.01)])
        if self.buttons:
            raise ValueError("a button is held; release it first (`up`), a corner reset would drag")
        for _ in range(a["reports"]):
            self._rel(-127, -127)
            time.sleep(a["interval"])
        print(f"sent {a['reports']} x (-127, -127)")

    def do_tap(self, arg: str) -> None:
        """tap X Y [step=10] [interval=0.02]: corner reset, move X,Y HID units, left click."""
        a = parse_args(arg, [("x", _int, REQUIRED), ("y", _int, REQUIRED), *self._move_spec()])
        self.do_corner("")
        self._move(a["x"], a["y"], a["step"], a["interval"])
        self._click(p.MOUSE_LEFT, 0.08)

    def do_swipe(self, arg: str) -> None:
        """swipe DX DY [steps=20] [interval=0.02] [hold=0.05]: press left, move (DX, DY) HID units
        over `steps` reports, release."""
        a = parse_args(arg, [("dx", _int, REQUIRED), ("dy", _int, REQUIRED), ("steps", _int, 20),
                             ("interval", float, 0.02), ("hold", float, 0.05)])
        if self.buttons:
            raise ValueError("a button is held; release it first (`up`)")
        steps = max(1, a["steps"])
        deltas = []
        for i in range(steps):
            deltas.append(tuple(round(v * (i + 1) / steps) - round(v * i / steps) for v in (a["dx"], a["dy"])))
        if any(abs(d) > 127 for pair in deltas for d in pair):
            raise ValueError("more than 127 units per report; raise steps")
        self._rel(buttons=p.MOUSE_LEFT)
        time.sleep(a["hold"])
        for sx, sy in deltas:
            self._rel(sx, sy, buttons=p.MOUSE_LEFT)
            time.sleep(a["interval"])
        time.sleep(a["hold"])
        self._rel(buttons=0)

    def do_scroll(self, arg: str) -> None:
        """scroll N [interval=0.05]: N wheel detents, one report each; positive = up (WCH doc)."""
        a = parse_args(arg, [("n", _int, REQUIRED), ("interval", float, 0.05)])
        for _ in range(abs(a["n"])):
            self._rel(wheel=1 if a["n"] > 0 else -1)
            time.sleep(a["interval"])

    def do_abs(self, arg: str) -> None:
        """abs X Y [buttons=0]: absolute pointer report on the 4096 x 4096 grid."""
        a = parse_args(arg, [("x", _int, REQUIRED), ("y", _int, REQUIRED), ("buttons", _int, 0)])
        self.hid.mouse_abs(a["x"], a["y"], a["buttons"])

    def do_abstest(self, arg: str) -> None:
        """abstest [pause=1.0]: absolute reports to the 4 corners and the centre, then asks what
        you saw. iOS is expected to ignore them; this records whether it does."""
        pause = parse_args(arg, [("pause", float, 1.0)])["pause"]
        for x, y in ((0, 0), (4095, 0), (4095, 4095), (0, 4095), (2048, 2048)):
            print(f"abs {x} {y}")
            self.hid.mouse_abs(x, y)
            time.sleep(pause)
        answer = self.ask("did the pointer jump to the corners and the centre? [y/n/describe]: ")
        self.log("observation", test="mouse_abs", answer=answer)

    # -- keyboard ------------------------------------------------------------------------------

    def _press(self, mods: int, keys: list[int], hold: float) -> None:
        self.hid.keyboard(mods, keys)
        time.sleep(hold)
        self.hid.keyboard(0, [])

    def do_type(self, arg: str) -> None:
        """type TEXT: type ASCII text (US layout); `\\n` is Enter, `\\t` is Tab.
        Pacing comes from `set key_hold=... key_gap=...`."""
        text = _unquote(arg)
        reports = keymap.text_reports(text)
        for mods, keys in reports:
            self.hid.keyboard(mods, keys)
            time.sleep(self.settings["key_hold"] if keys else self.settings["key_gap"])
        print(f"typed {len(text)} characters")

    def do_key(self, arg: str) -> None:
        """key COMBO [COMBO ...]: press and release each, e.g. `key cmd+space`, `key esc`."""
        combos = shlex.split(arg)
        if not combos:
            raise ValueError("usage: key COMBO [COMBO ...]")
        for combo in combos:
            self._press(*keymap.parse_combo(combo), 0.05)
            time.sleep(0.1)

    def do_trial(self, arg: str) -> None:
        """trial COMBO [n=20] [wait=1.5] [close=esc] [auto=no]: send a shortcut n times and record
        after each try whether it worked (you answer y/n, q stops). `close` is sent after each try
        (`none` to skip). The Cmd+Space test is `trial cmd+space`."""
        a = parse_args(arg, [("combo", str, REQUIRED), ("n", _int, 20), ("wait", float, 1.5),
                             ("close", str, "esc"), ("auto", _bool, False)])
        combo, close = keymap.parse_combo(a["combo"]), None
        if a["close"] != "none":
            close = keymap.parse_combo(a["close"])
        results = []
        for i in range(1, a["n"] + 1):
            self._press(*combo, 0.05)
            time.sleep(a["wait"])
            ok = None
            if not a["auto"]:
                answer = self.ask(f"[{i}/{a['n']}] did {a['combo']} work? [y/n/q]: ").strip().lower()
                if answer.startswith("q"):
                    break
                ok = answer.startswith("y")
            results.append(ok)
            self.log("trial", combo=a["combo"], attempt=i, ok=ok)
            if close:
                self._press(*close, 0.05)
                time.sleep(0.8)
        worked = sum(1 for r in results if r)
        answered = sum(1 for r in results if r is not None)
        print(f"{a['combo']}: {worked}/{answered} worked ({len(results)} sent)")
        self.log("trial_summary", combo=a["combo"], sent=len(results), answered=answered, worked=worked)

    def do_cmdspace(self, arg: str) -> None:
        """cmdspace [n=20] [...]: shorthand for `trial cmd+space ...` (docs/phase0-checklist.md)."""
        self.do_trial(f"cmd+space {arg}")

    def do_media(self, arg: str) -> None:
        """media NAME [hold=0.1]: tap a multimedia key. Names: volume_up volume_down mute
        play_pause next_track prev_track ... (see protocol.MEDIA_KEYS)."""
        a = parse_args(arg, [("name", str, REQUIRED), ("hold", float, 0.1)])
        if a["name"] not in p.MEDIA_KEYS:
            raise ValueError(f"unknown media key; one of: {' '.join(p.MEDIA_KEYS)}")
        self.hid.media(p.MEDIA_KEYS[a["name"]])
        time.sleep(a["hold"])
        self.hid.media(0)

    def do_acpi(self, arg: str) -> None:
        """acpi power|sleep|wake [hold=0.1]: tap an ACPI key (effect on iOS unknown)."""
        a = parse_args(arg, [("name", str, REQUIRED), ("hold", float, 0.1)])
        if a["name"] not in p.ACPI_KEYS:
            raise ValueError(f"unknown ACPI key; one of: {' '.join(p.ACPI_KEYS)}")
        self.hid.acpi(p.ACPI_KEYS[a["name"]])
        time.sleep(a["hold"])
        self.hid.acpi(0)

    # -- measurements and misc -----------------------------------------------------------------

    def do_bench(self, arg: str) -> None:
        """bench [n=100]: time GET_INFO and acknowledged mouse reports, then fire-and-forget
        throughput. Sends zero-movement reports only."""
        n = parse_args(arg, [("n", _int, 100)])["n"]
        info, acked = [], []
        for _ in range(min(n, 50)):
            t = time.monotonic()
            self.hid.info()
            info.append(time.monotonic() - t)
        saved = self.hid.wait_ack
        try:
            self.hid.wait_ack = True
            for _ in range(n):
                t = time.monotonic()
                self._rel()
                acked.append(time.monotonic() - t)
            self.hid.wait_ack = False
            lost0, err0 = self.hid.stats["lost_acks"], len(self.hid.async_errors)
            t = time.monotonic()
            for _ in range(n):
                self._rel()
            send_s = time.monotonic() - t
            self.hid.sync()
            lost, errors = self.hid.stats["lost_acks"] - lost0, len(self.hid.async_errors) - err0
        finally:
            self.hid.wait_ack = saved
        wire_ms = (11 + 7) * 10 / self.hid.baud * 1000
        rate = n / send_s if send_s else float("inf")
        print(f"GET_INFO round trip:       {_stats_ms(info)}")
        print(f"mouse report with ack:     {_stats_ms(acked)}  -> {1 / statistics.fmean(acked):.0f} reports/s")
        print(f"mouse report, no ack wait: {rate:.0f} reports/s; lost acks {lost}, error replies {errors}")
        print(f"wire time per report + ack at {self.hid.baud} baud: {wire_ms:.1f} ms")
        self.log("bench", n=n, baud=self.hid.baud, info_ms=[round(s * 1000, 2) for s in info],
                 acked_ms=[round(s * 1000, 2) for s in acked], no_ack_rate=round(rate, 1), lost_acks=lost, error_replies=errors)

    def do_raw(self, arg: str) -> None:
        """raw HEX...: send bytes and print what comes back for 0.3 s. A full frame (57 AB ...)
        goes out verbatim, bad checksum included; otherwise the first byte is CMD and the rest DATA,
        framed for you. Flash-writing commands are refused: use `cfg`."""
        data = bytes.fromhex(arg.replace(",", " "))
        if not data:
            raise ValueError("usage: raw HEX...")
        if data[:2] == p.HEADER:
            frame, command = data, data[3] if len(data) > 3 else None
        else:
            frame, command = p.encode(data[0], data[1:], self.hid.addr), data[0]
        if command in FLASH_WRITES:
            raise ValueError("raw refuses commands that write chip flash; use `cfg set/restore/default`")
        print(f"tx {frame.hex(' ')}")
        frames = self.hid.transact_raw(frame)
        for f in frames:
            kind = "error" if f.is_error else "ok"
            status = f" status {f.status:#04x}" if f.status is not None else ""
            print(f"rx {f.to_bytes().hex(' ')}  ({kind} reply to {f.request_cmd:#04x}{status})")
        if not frames:
            print("no reply")

    def do_note(self, arg: str) -> None:
        """note TEXT: write an observation into the log, e.g. `note pointer visible in capture`."""
        self.log("note", text=arg.strip())
        print("noted")

    def do_sleep(self, arg: str) -> None:
        """sleep SECONDS: pause (useful in `;`-separated command lists)."""
        time.sleep(parse_args(arg, [("seconds", float, REQUIRED)])["seconds"])

    def do_release(self, arg: str) -> None:
        """release: release every key and mouse button."""
        self.buttons = 0
        self.hid.release_all()

    def do_set(self, arg: str) -> None:
        """set [NAME=VALUE ...]: show or change defaults: step, interval (move), key_hold, key_gap (type)."""
        for tok in shlex.split(arg):
            name, _, value = tok.partition("=")
            if name not in self.settings:
                raise ValueError(f"unknown setting {name!r}; known: {', '.join(self.settings)}")
            self.settings[name] = type(self.settings[name])(value)
        print("  ".join(f"{k}={v}" for k, v in self.settings.items()))
        self.log("settings", **self.settings)

    def do_sim(self, arg: str) -> None:
        """sim: (--fake only) show the simulated pointer, typed text and recent events."""
        if not self.fake:
            raise ValueError("sim only works with --fake")
        chip = self.hid.chip
        pt = chip.pointer
        print(f"pointer ({pt.x:.1f}, {pt.y:.1f}) on {pt.width:.0f}x{pt.height:.0f}, buttons {pt.buttons:#x}")
        print(f"typed text {chip.keyboard.text!r}; shortcuts {chip.keyboard.shortcuts}")
        for e in chip.events[-10:]:
            print("  " + " ".join(f"{k}={v}" for k, v in e.items() if k not in ("t", "seq")))

    def _print_sim_changes(self) -> None:
        chip = self.hid.chip
        for e in chip.events:
            if e["seq"] > self._sim_seen and e["event"] != "char":
                print("  [sim] " + " ".join(str(v) for k, v in e.items() if k not in ("t", "seq")))
        self._sim_seen = chip.event_seq
        pos = (round(chip.pointer.x, 1), round(chip.pointer.y, 1))
        if pos != self._sim_pos:
            if self._sim_pos is not None:
                print(f"  [sim] pointer at {pos}")
            self._sim_pos = pos
        if chip.keyboard.text != self._sim_text:
            self._sim_text = chip.keyboard.text
            print(f"  [sim] text {self._sim_text!r}")

    def do_quit(self, arg: str) -> bool:
        """quit: release everything and exit."""
        return True

    do_exit = do_quit

    def do_EOF(self, arg: str) -> bool:
        print()
        return True


def _auto_port() -> str:
    cands = candidate_ports()
    if len(cands) == 1:
        print(f"using {cands[0].device} ({cands[0].chip or cands[0].description})")
        return cands[0].device
    if not cands:
        raise SystemExit("no USB serial port found: plug in the cable, or pass --port (see `python -m ihc.hid.scan --list`)")
    listing = "\n".join(f"  {c.device}  {c.chip or c.description}" for c in cands)
    raise SystemExit(f"several USB serial ports; pass --port:\n{listing}")


def main(argv: list[str] | None = None, *, ask=input) -> int:
    ap = argparse.ArgumentParser(prog="hidtest", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--port", help="serial port, e.g. /dev/serial/by-id/... (default: the only USB serial port)")
    src.add_argument("--fake", action="store_true", help="talk to a simulated chip instead of hardware")
    ap.add_argument("--baud", type=int, default=9600, help="default 9600 (factory setting)")
    ap.add_argument("--addr", type=_int, default=0, help="chip address, default 0")
    ap.add_argument("--timeout", type=float, default=p.REPLY_TIMEOUT_S, help="reply timeout in seconds")
    ap.add_argument("--no-ack", action="store_true", help="do not wait for acks on HID reports")
    ap.add_argument("--log", help="JSON-lines log path (default docs/test-logs/hidtest-<time>.jsonl)")
    ap.add_argument("--no-log", action="store_true", help="do not write a log")
    ap.add_argument("command", nargs=argparse.REMAINDER, help="command(s) to run instead of the shell, separated by ';'")
    args = ap.parse_args(argv)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    log_path = None if args.no_log else Path(args.log or LOG_DIR / f"hidtest-{'fake-' if args.fake else ''}{stamp}.jsonl")
    log = EventLog(log_path)
    opts = dict(addr=args.addr, timeout=args.timeout, wait_ack=not args.no_ack, trace=log)
    if args.fake:
        from ihc.hid.fake import FakeBackend

        hid = FakeBackend(baud=args.baud, simulate_timing=True, **opts)
        port_info = {"fake": True}
    else:
        port = args.port or _auto_port()
        try:
            hid = CH9329Backend(port, args.baud, **opts)
        except HidError as e:
            log("error", kind=type(e).__name__, error=str(e))
            print(f"error: {e}", file=sys.stderr)
            return 2
        port_info = next((pi.to_dict() for pi in list_serial_ports() if pi.device == port or port in pi.by_id + pi.by_path), {})
    log("session_start", argv=sys.argv if argv is None else argv, port=hid.port, baud=hid.baud, addr=args.addr,
        wait_ack=hid.wait_ack, port_info=port_info, python=platform.python_version(), platform=platform.platform())

    shell = HidShell(hid, log, ask=ask, fake=args.fake)
    try:
        if args.command:
            shell.run_script(" ".join(args.command))
        else:
            print("hidtest: `help` lists commands, `help <command>` explains one; Ctrl+C stops a running command.")
            if log_path:
                print(f"log: {log_path}")
            shell.onecmd("info")
            shell.postcmd(False, "info")
            while True:
                try:
                    shell.cmdloop(intro="")
                    break
                except KeyboardInterrupt:
                    print("^C (type quit to exit)")
    finally:
        hid.release_all()
        log("session_end", stats=dict(hid.stats), async_errors=len(hid.async_errors))
        hid.close()
        log.close()
    if log_path and args.command:
        print(f"log: {log_path}")
    return 1 if shell.failed else 0


if __name__ == "__main__":
    sys.exit(main())
