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
    notes: list[dict] = []
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
                "ocr": list(getattr(ev, "ocr", []) or []),
            }
        )
        for n in getattr(ev, "notes", []) or []:
            if n not in notes:
                notes.append(n)
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
        "review_items": notes,  # REV-1: every note with its level
        "review_level": review_level(notes),
        "rules_version": rules_version,
    }


# 規則 REV-1：資訊提示只留紀錄；建議回查與處理失敗才進複查清單、才標黃
LEVEL_ORDER = {"無": 0, "資訊提示": 1, "建議回查": 2, "處理失敗": 3}
FLAG_LEVELS = ("建議回查", "處理失敗")


def review_level(notes: list[dict]) -> str:
    return max((n["level"] for n in notes), key=LEVEL_ORDER.get, default="無")


def story_records(by_ch: dict[str, list], run_id: str | None = None) -> list[dict]:
    """EVD-1: one record per segment (story unit, ad, outage) with its boundary evidence."""
    out = []
    for ch, events in by_ch.items():
        for ev in events:
            b = ev.signals.get("boundary") or {}
            out.append({
                "run_id": run_id,
                "channel": ch,
                "story_id": ev.story_id,
                "type": ev.type.value,
                "label": ev.label,
                "start_sec": ev.start_sec,
                "end_sec": ev.signals.get("end_sec"),
                "bar_text": ev.source.bar_text,
                "confidence": ev.confidence,
                "fallback": ev.fallback,
                "boundary_kind": b.get("kind"),
                "score": b.get("score"),
                "score_source": b.get("score_source"),
                "reason_codes": b.get("reason_codes", []),
                "evidence": b.get("evidence", {}),
                "review_required": review_level(getattr(ev, "notes", []) or []) in FLAG_LEVELS
                if getattr(ev, "notes", None) else (bool(b.get("review_required")) or ev.confidence == "low" or bool(ev.fallback)),
                "review_level": review_level(getattr(ev, "notes", []) or []),
                "review_items": list(getattr(ev, "notes", []) or []),
                "ocr": list(getattr(ev, "ocr", []) or []),
                "signals": ev.signals.get("ids", []),
            })
    return out


def save_stories(by_ch: dict[str, list], path: Path, run_id: str | None = None) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for r in story_records(by_ch, run_id):
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def review_priority(rec: dict) -> int:
    if "review_level" in rec:
        return {"處理失敗": -1, "建議回查": PRIORITY["fallback"], "資訊提示": PRIORITY["ad_edge"]}.get(
            rec["review_level"], PRIORITY["other"])
    if rec["fallback"]:
        return PRIORITY["fallback"]
    if rec["confidence"] == "low":
        return PRIORITY["low"]
    if any(i["type"] in (EventType.AD.value, EventType.OUTAGE.value) for i in rec["items"]):
        return PRIORITY["ad_edge"]
    return PRIORITY["other"]


# 規則 QA-1：執行狀態和品質狀態分開。程式跑完（execution）不等於可以直接交付（quality）。
AUTO_ACCEPTED = "AUTO_ACCEPTED"  # passed every automatic check, not looked at by a person
REVIEW_REQUIRED = "REVIEW_REQUIRED"  # low confidence, fallback, or a boundary without corroboration
HUMAN_APPROVED = "HUMAN_APPROVED"  # a reviewer confirmed or corrected it (apply-corrections)
REJECTED = "REJECTED"  # a reviewer marked it unusable


def quality_of(rec: dict) -> dict:
    """QA-1: the review state of one cell as data — the yellow fill of the review copy is drawn
    from this, never the other way round."""
    if rec.get("review_items") is not None and "review_level" in rec:
        needs = rec["review_level"] in FLAG_LEVELS  # REV-1 (new records)
    else:  # records written before REV-1
        needs = (bool(rec.get("fallback")) or rec.get("confidence") == "low"
                 or any((i.get("boundary") or {}).get("review_required") for i in rec.get("items", [])))
    codes = []
    for i in rec.get("items", []):
        codes += [c for c in (i.get("boundary") or {}).get("reason_codes", []) if c not in codes]
    return {"status": REVIEW_REQUIRED if needs else AUTO_ACCEPTED, "reason_codes": codes,
            "reasons": list(rec.get("reasons", [])), "reviewer": None, "reviewed_at": None, "corrected_value": None}


def build(cells: dict[str, dict[str, Cell]], rules_version: str, run_id: str | None = None) -> list[dict]:
    recs = [record(c, rules_version) for by_min in cells.values() for c in by_min.values()]
    for r in recs:
        r["run_id"] = run_id
        r["quality"] = quality_of(r)
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
