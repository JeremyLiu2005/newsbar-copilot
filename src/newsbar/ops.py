"""Operations helpers: hourly folder watcher (NFR-24) and frame retention (NFR-17).

Watcher convention — the internal recorder drops one file per channel per hour:
    {channel}_{YYYYMMDD}_{HHMM}.mp4      e.g. tvbs_20261106_1200.mp4
A slot is processed once every expected channel has arrived (or after
`grace_sec`, with whatever is there — a missing channel must not block the rest).
State lives in {out_dir}/.watch_state.json so a restart never reprocesses a slot.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path

NAME = re.compile(r"^(?P<ch>[a-z0-9]+)_(?P<date>\d{8})_(?P<hhmm>\d{4})\.(mp4|mov|ts|mkv)$", re.I)


def slots(inbox: Path) -> dict[tuple[str, str], dict[str, Path]]:
    out: dict[tuple[str, str], dict[str, Path]] = {}
    for p in sorted(inbox.iterdir()):
        m = NAME.match(p.name)
        if m:
            out.setdefault((m["date"], m["hhmm"]), {})[m["ch"].lower()] = p
    return out


def _state(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"done": []}


def ready_slots(inbox: Path, channels: list[str], out_dir: Path, grace_sec: float = 900, now: float | None = None):
    now = time.time() if now is None else now
    done = set(_state(out_dir / ".watch_state.json")["done"])
    for (date, hhmm), files in sorted(slots(inbox).items()):
        key = f"{date}_{hhmm}"
        if key in done:
            continue
        complete = all(c in files for c in channels)
        oldest = min(p.stat().st_mtime for p in files.values())
        if complete or now - oldest > grace_sec:
            yield key, date, hhmm, {c: files[c] for c in channels if c in files}


def mark_done(out_dir: Path, key: str) -> None:
    path = out_dir / ".watch_state.json"
    st = _state(path)
    st["done"] = sorted(set(st["done"]) | {key})
    out_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")


def cleanup_frames(workdir: Path, older_than_days: float, now: float | None = None) -> int:
    """Delete per-second crops older than N days; text results are kept (NFR-17)."""
    now = time.time() if now is None else now
    removed = 0
    for frames in workdir.rglob("frames"):
        if frames.is_dir() and now - frames.stat().st_mtime > older_than_days * 86400:
            shutil.rmtree(frames)
            removed += 1
    return removed
