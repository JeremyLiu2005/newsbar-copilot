"""Per-cell evidence records (spec §9.1, §9.2)."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from .schemas import Cell, EventType

PRIORITY = {"fallback": 0, "low": 1, "ad_edge": 2, "other": 3}


def record(cell: Cell, rules_version: str) -> dict:
    items = []
    signals: list[str] = []
    fallbacks = []
    reasons: list[str] = []
    conf = "high"
    order = {"high": 0, "medium": 1, "low": 2}
    for ev in cell.events:
        items.append(
            {
                "type": ev.type.value,
                "label": ev.label,
                "start_sec": ev.start_sec,
                "bar_text": ev.source.bar_text,
                "asr_text": ev.source.asr_text,
                "frame": ev.source.frame,
                "boundary": ev.signals.get("boundary"),
            }
        )
        signals.extend(ev.signals.get("ids", []))
        if ev.fallback:
            fallbacks.append(ev.fallback)
        reasons.extend(r for r in ev.reasons if r not in reasons)
        if order.get(ev.confidence, 0) > order[conf]:
            conf = ev.confidence
    return {
        "channel": cell.channel,
        "minute": cell.minute,
        "value": cell.value,
        "items": items,
        "signals": sorted(set(signals)),
        "confidence": conf,
        "fallback": ",".join(fallbacks) or None,
        "reasons": reasons,
        "rules_version": rules_version,
    }


def story_records(by_ch: dict[str, list]) -> list[dict]:
    """EVD-1: one record per segment (story unit, ad, outage) with its boundary evidence."""
    out = []
    for ch, events in by_ch.items():
        for ev in events:
            b = ev.signals.get("boundary") or {}
            out.append({
                "channel": ch,
                "story_id": ev.story_id,
                "type": ev.type.value,
                "label": ev.label,
                "start_sec": ev.start_sec,
                "end_sec": ev.signals.get("end_sec"),
                "bar_text": ev.source.bar_text,
                "confidence": ev.confidence,
                "fallback": ev.fallback,
                "boundary_probability": b.get("boundary_probability"),
                "boundary_kind": b.get("kind"),
                "evidence": b.get("evidence", {}),
                "review_required": bool(b.get("review_required")) or ev.confidence == "low" or bool(ev.fallback),
                "signals": ev.signals.get("ids", []),
            })
    return out


def save_stories(by_ch: dict[str, list], path: Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for r in story_records(by_ch):
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def review_priority(rec: dict) -> int:
    if rec["fallback"]:
        return PRIORITY["fallback"]
    if rec["confidence"] == "low":
        return PRIORITY["low"]
    if any(i["type"] in (EventType.AD.value, EventType.OUTAGE.value) for i in rec["items"]):
        return PRIORITY["ad_edge"]
    return PRIORITY["other"]


def build(cells: dict[str, dict[str, Cell]], rules_version: str) -> list[dict]:
    recs = [record(c, rules_version) for by_min in cells.values() for c in by_min.values()]
    return sorted(recs, key=lambda r: (review_priority(r), r["minute"], r["channel"]))


FAILED_PRIORITY = -1  # a channel with no output at all sorts above every cell


def save(records: list[dict], jsonl: Path, csv_path: Path, failed: dict[str, str] | None = None) -> None:
    """jsonl = per-cell evidence; csv = the review list (複查清單). A channel that failed has no
    cells to list, so it gets its own first row there — never a mark inside the workbook."""
    jsonl.parent.mkdir(parents=True, exist_ok=True)
    with jsonl.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["priority", "channel", "minute", "value", "confidence", "fallback", "signals", "bar_text", "start_sec",
                    "reasons"])
        for ch, msg in (failed or {}).items():
            w.writerow([FAILED_PRIORITY, ch, "", "", "failed", "CHANNEL-FAILED", "", "", "", f"整台失敗：{msg}"])
        for r in records:
            first = r["items"][0] if r["items"] else {}
            w.writerow(
                [
                    review_priority(r),
                    r["channel"],
                    r["minute"],
                    r["value"],
                    r["confidence"],
                    r["fallback"] or "",
                    " ".join(r["signals"]),
                    first.get("bar_text") or "",
                    first.get("start_sec", ""),
                    "；".join(r.get("reasons", [])),
                ]
            )
