import os
import struct

from conftest import load_tool

loopback = load_tool("hid_loopback")
capture_check = load_tool("capture_check")


def test_summarize_rel_counts_lost_and_merged_reports():
    sent = [1.0 + i * 0.01 for i in range(10)]
    events = [(s + 0.004, loopback.EV_REL, loopback.REL_X, 1) for s in sent]
    r = loopback.summarize_rel(events, sent, 1)
    assert r["lost_units"] == 0 and r["merged_reports"] == 0 and r["latency_ms_p50"] == 4.0
    assert r["send_gap_ms"]["p50"] == 10.0 and r["arrival_gap_ms"]["max"] == 10.0
    merged = events[:8] + [(sent[9] + 0.004, loopback.EV_REL, loopback.REL_X, 2)]
    r = loopback.summarize_rel(merged, sent, 1)
    assert r["lost_units"] == 0 and r["merged_reports"] == 1
    r = loopback.summarize_rel(events[:7], sent, 1)
    assert r["lost_units"] == 3


def test_event_reader_parses_input_events(tmp_path, monkeypatch):
    r, w = os.pipe()
    os.write(w, loopback.EVENT.pack(5, 250000, loopback.EV_REL, loopback.REL_X, -3))
    reader = loopback.EventReader.__new__(loopback.EventReader)
    reader.fds = [r]
    events = reader.drain(0.05)
    assert events == [(5.25, loopback.EV_REL, loopback.REL_X, -3)]
    os.close(r)
    os.close(w)


def test_find_nodes_and_list_devices_do_not_crash():
    assert isinstance(loopback.find_nodes(), list)
    assert capture_check.main(["list"]) == 0
