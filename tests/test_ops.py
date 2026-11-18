"""Watcher slots and frame retention (NFR-24, NFR-17)."""

import os
import time

from newsbar.ops import cleanup_frames, mark_done, ready_slots, slots

CH = ["tvbs", "ettv", "ftv", "cts"]


def touch(p, age=0):
    p.write_bytes(b"x")
    t = time.time() - age
    os.utime(p, (t, t))


def test_slot_waits_for_all_channels_then_grace(tmp_path):
    inbox = tmp_path / "in"
    inbox.mkdir()
    for ch in CH[:3]:
        touch(inbox / f"{ch}_20261106_1200.mp4")
    touch(inbox / "readme.txt")
    assert set(slots(inbox)[("20261106", "1200")]) == set(CH[:3])
    assert list(ready_slots(inbox, CH, tmp_path / "out")) == []  # cts missing, still within grace
    later = time.time() + 1000
    got = list(ready_slots(inbox, CH, tmp_path / "out", grace_sec=900, now=later))
    assert [g[0] for g in got] == ["20261106_1200"] and set(got[0][3]) == set(CH[:3])


def test_complete_slot_runs_once(tmp_path):
    inbox = tmp_path / "in"
    inbox.mkdir()
    for ch in CH:
        touch(inbox / f"{ch}_20261106_1300.mp4")
    out = tmp_path / "out"
    assert [g[0] for g in ready_slots(inbox, CH, out)] == ["20261106_1300"]
    mark_done(out, "20261106_1300")
    assert list(ready_slots(inbox, CH, out)) == []


def test_cleanup_only_old_frames(tmp_path):
    old = tmp_path / "w" / "a" / "tvbs" / "frames"
    new = tmp_path / "w" / "b" / "tvbs" / "frames"
    for d in (old, new):
        d.mkdir(parents=True)
        (d.parent / "obs.jsonl").write_text("{}")
    t = time.time() - 10 * 86400
    os.utime(old, (t, t))
    assert cleanup_frames(tmp_path / "w", 7) == 1
    assert not old.exists() and new.exists() and (old.parent / "obs.jsonl").exists()
