import json

import pytest

from conftest import load_tool

hidtest = load_tool("hidtest")


def run(args, answers=(), tmp_path=None):
    replies = iter(answers)
    return hidtest.main(args, ask=lambda q: next(replies))


def test_script_mode_runs_commands(tmp_path, capsys):
    log = tmp_path / "s.jsonl"
    code = run(["--fake", "--log", str(log), "info; move 20 -10 step=10; click; type Hi!; key cmd+space esc; sim"])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "USB connected" in out and "typed 3 characters" in out and "'Hi!'" in out
    events = [json.loads(line)["event"] for line in log.read_text().splitlines()]
    assert events[0] == "session_start" and events[-1] == "session_end"
    assert "tx" in events and "rx" in events


def test_unknown_command_fails(capsys):
    assert run(["--fake", "--no-log", "nope"]) == 1
    assert "unknown command" in capsys.readouterr().out


def test_bad_arguments_fail(capsys):
    assert run(["--fake", "--no-log", "move 1"]) == 1
    assert "missing argument" in capsys.readouterr().out


def test_cfg_set_requires_yes_and_backs_up(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(hidtest, "LOG_DIR", tmp_path)
    assert run(["--fake", "--no-log", "cfg set baud=115200"], answers=["no"]) == 0
    assert "not written" in capsys.readouterr().out
    assert run(["--fake", "--no-log", "cfg set baud=115200; cfg"], answers=["YES"]) == 0
    out = capsys.readouterr().out
    assert "baud: 9600 (0x2580) -> 115200 (0x1c200)" in out
    assert "read back matches" in out and "undo:" in out
    assert "baud                   115200" in out
    assert list(tmp_path.glob("ch9329-cfg-*.json"))


def test_cfg_refuses_breaking_serial_mode(capsys):
    assert run(["--fake", "--no-log", "cfg set serial_mode=1"]) == 1
    assert "serial_mode must stay" in capsys.readouterr().out


def test_raw_refuses_flash_writes(capsys):
    assert run(["--fake", "--no-log", "raw 0c"]) == 1
    assert "refuses" in capsys.readouterr().out


def test_trial_auto_mode(capsys):
    assert run(["--fake", "--no-log", "trial cmd+space n=2 wait=0 auto=yes"]) == 0
    assert "cmd+space: 0/0 worked (2 sent)" in capsys.readouterr().out


def test_trial_with_answers(capsys):
    assert run(["--fake", "--no-log", "cmdspace n=3 wait=0"], answers=["y", "n", "y"]) == 0
    assert "cmd+space: 2/3 worked (3 sent)" in capsys.readouterr().out


def test_swipe_and_tap_in_sim(capsys):
    assert run(["--fake", "--no-log", "tap 50 80; swipe 0 -100 steps=5 interval=0"]) == 0
    out = capsys.readouterr().out
    assert "click left" in out and "drag left" in out


def test_parse_args_helper():
    spec = [("a", int, hidtest.REQUIRED), ("b", float, 1.5)]
    assert hidtest.parse_args("3", spec) == {"a": 3, "b": 1.5}
    assert hidtest.parse_args("b=2 4", spec) == {"a": 4, "b": 2.0}
    with pytest.raises(ValueError):
        hidtest.parse_args("1 2 3", spec)
    with pytest.raises(ValueError):
        hidtest.parse_args("c=1", spec)
