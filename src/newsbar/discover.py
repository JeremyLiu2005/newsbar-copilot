"""One-click input discovery: point `newsbar run --input-dir` at a folder and it finds each
channel's recording (by channel key or name in the file name) and the template workbook."""

from __future__ import annotations

import re
from pathlib import Path

VIDEO_EXT = {".ts", ".mp4", ".mov", ".mkv", ".m4v"}


def _matches(stem: str, key: str, name: str) -> bool:
    s = stem.lower()
    toks = [t for t in re.split(r"[^a-z0-9]+", s) if t]
    if key.lower() in toks or (name and name.lower() in s):
        return True
    k = key.lower()
    return s.startswith(k) and not s[len(k):len(k) + 1].isalpha()  # tvbs20261106


def find_videos(folder: Path, channels: dict[str, dict]) -> tuple[dict[str, Path], list[str]]:
    """{channel key: video path} plus human-readable warnings (missing, ambiguous, duplicates)."""
    warns: list[str] = []
    files = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXT
                   and not p.name.startswith("."))
    cand: dict[str, list[Path]] = {k: [] for k in channels}
    for p in files:
        hit = [k for k, c in channels.items() if _matches(p.stem, k, str(c.get("name") or ""))]
        if len(hit) > 1:
            warns.append(f"檔名 {p.name} 同時符合 {', '.join(hit)}，略過（請改檔名）")
        elif hit:
            cand[hit[0]].append(p)
    found: dict[str, Path] = {}
    for k, ps in cand.items():
        if not ps:
            continue
        best = max(ps, key=lambda q: q.stat().st_size)
        if len(ps) > 1:
            warns.append(f"{k} 有 {len(ps)} 支影片（{', '.join(q.name for q in ps)}），用最大的 {best.name}")
        found[k] = best
    return found, warns


def find_template(folder: Path) -> tuple[Path | None, list[str]]:
    xs = sorted(p for p in folder.glob("*.xlsx") if not p.name.startswith(("~$", ".")))
    if not xs:
        return None, []
    pref = [p for p in xs if any(w in p.name.lower() for w in ("template", "範本", "監看"))]
    pick = (pref or xs)[0]
    warns = [f"資料夾裡有 {len(xs)} 個 .xlsx，用 {pick.name} 當範本"] if len(xs) > 1 else []
    return pick, warns
