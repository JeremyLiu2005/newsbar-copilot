"""Evaluation (spec §11 metrics; FR-23, FR-30, NFR-26).

Gold standard = a 12監看-shaped xlsx (or {channel: {minute: value}} dict)
filled by two annotators and reconciled. Metrics:
  * change F1   — story-start minutes, ±tolerance minutes, greedy 1:1 matching
  * ad F1       — per-minute ad state (expanded from the "廣告 … blank" notation)
  * auto-fill   — share of decisions the system made without a human
  * label pairs — (system, gold) label pairs for the rubric review
  * error types — 漏換題 / 過度換題 / 包框誤判 … shared with the review console
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from openpyxl import load_workbook

from .excel_writer import SHEET, locate
from .minute_mapper import minute_labels
from .schemas import ErrorType

AD, OUTAGE = "廣告", "訊號中斷"


def read_sheet(path: Path, channel_headers: dict[str, str], sheet: str = SHEET) -> dict[str, dict[str, str]]:
    ws = load_workbook(path, data_only=True)[sheet]
    lay = locate(ws, channel_headers)
    out: dict[str, dict[str, str]] = {}
    for ch, col in lay.channel_cols.items():
        out[ch] = {}
        for m, r in lay.minute_rows.items():
            v = ws.cell(r, col).value
            if v not in (None, ""):
                out[ch][m] = str(v).strip()
    return out


def items(value: str) -> list[str]:
    return [x.strip() for x in value.split("/") if x.strip()]


def states(col: dict[str, str], minutes: list[str]) -> dict[str, str]:
    """Expand the sparse notation: blank continues the previous state."""
    cur = ""
    out = {}
    for m in minutes:
        if m in col:
            cur = items(col[m])[-1]
        out[m] = cur
    return out


def story_starts(col: dict[str, str]) -> list[tuple[str, str]]:
    return [(m, it) for m, v in sorted(col.items()) for it in items(v) if it not in (AD, OUTAGE)]


@dataclass
class PRF:
    tp: int = 0
    fp: int = 0
    fn: int = 0

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else 1.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else 1.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0

    def as_dict(self) -> dict:
        return {"tp": self.tp, "fp": self.fp, "fn": self.fn, "precision": round(self.precision, 3),
                "recall": round(self.recall, 3), "f1": round(self.f1, 3)}


@dataclass
class Report:
    change: dict
    ad: dict
    per_channel: dict
    label_pairs: list[dict]
    errors: list[dict]
    counts: dict = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)


def _mi(m: str) -> int:
    return int(m[:2]) * 60 + int(m[2:])


def evaluate(
    system: dict[str, dict[str, str]],
    gold: dict[str, dict[str, str]],
    start: str = "1200",
    n_minutes: int = 60,
    tolerance: int = 1,
    only_minutes: tuple[str, str] | None = None,
) -> Report:
    minutes = minute_labels(start, n_minutes)
    if only_minutes:
        lo, hi = only_minutes
        minutes = [m for m in minutes if lo <= m <= hi]
    mset = set(minutes)
    change, ad = PRF(), PRF()
    per_channel: dict = {}
    pairs: list[dict] = []
    errors: list[dict] = []
    for ch in gold:
        s_col = {m: v for m, v in system.get(ch, {}).items() if m in mset}
        g_col = {m: v for m, v in gold[ch].items() if m in mset}
        c = PRF()
        s_starts = story_starts(s_col)
        g_starts = story_starts(g_col)
        used = set()
        for gm, glabel in g_starts:
            best = None
            for j, (sm, slabel) in enumerate(s_starts):
                if j in used:
                    continue
                d = abs(_mi(sm) - _mi(gm))
                if d <= tolerance and (best is None or d < best[0]):
                    best = (d, j, sm, slabel)
            if best:
                used.add(best[1])
                c.tp += 1
                pairs.append({"channel": ch, "gold_minute": gm, "system_minute": best[2],
                              "gold": glabel, "system": best[3]})
            else:
                c.fn += 1
                errors.append({"channel": ch, "minute": gm, "type": ErrorType.MISSED_CHANGE.value, "gold": glabel})
        for j, (sm, slabel) in enumerate(s_starts):
            if j not in used:
                c.fp += 1
                errors.append({"channel": ch, "minute": sm, "type": ErrorType.SPURIOUS_CHANGE.value, "system": slabel})
        a = PRF()
        s_state, g_state = states(s_col, minutes), states(g_col, minutes)
        for m in minutes:
            sa, ga = s_state[m] == AD, g_state[m] == AD
            if sa and ga:
                a.tp += 1
            elif sa:
                a.fp += 1
                errors.append({"channel": ch, "minute": m, "type": ErrorType.FRAMED_AD_MISS.value, "detail": "誤判為廣告"})
            elif ga:
                a.fn += 1
                errors.append({"channel": ch, "minute": m, "type": ErrorType.FRAMED_AD_MISS.value, "detail": "漏判廣告"})
        for x, y in ((change, c), (ad, a)):
            x.tp, x.fp, x.fn = x.tp + y.tp, x.fp + y.fp, x.fn + y.fn
        per_channel[ch] = {"change": c.as_dict(), "ad": a.as_dict()}
    counts = {
        "changes": sum(len(story_starts(v)) for v in system.values()),
        "ads": sum(1 for v in system.values() for x in v.values() for it in items(x) if it == AD),
    }
    return Report(change.as_dict(), ad.as_dict(), per_channel, pairs, errors, counts)


def processing_report(evidence: list[dict], warnings: dict[str, list[str]] | None = None,
                      timings: dict[str, float] | None = None) -> dict:
    """FR-30: counts per channel plus quality signals for the daily report (FR-18)."""
    by: dict[str, dict] = {}
    for r in evidence:
        d = by.setdefault(r["channel"], {"cells": 0, "changes": 0, "ads": 0, "low_conf": 0, "fallback": 0})
        d["cells"] += 1
        for it in r["items"]:
            if it["type"] == "ad":
                d["ads"] += 1
            elif it["type"] in ("news", "weather"):
                d["changes"] += 1
        d["low_conf"] += r["confidence"] == "low"
        d["fallback"] += bool(r["fallback"])
    for d in by.values():
        d["fallback_rate"] = round(d["fallback"] / d["cells"], 3) if d["cells"] else 0.0
        d["alert"] = d["fallback_rate"] > 0.2  # FB-R3
    return {"channels": by, "warnings": warnings or {}, "timings_sec": timings or {}}
