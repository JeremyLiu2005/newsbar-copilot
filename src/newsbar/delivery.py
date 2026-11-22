"""Delivery check (可交付, QA-2): is this workbook ready to hand over, and what still needs a person?

Runs after the evidence is built and before the workbook is written. It never blocks writing the
sheet (R20: every cell is still filled); it states plainly what the sheet is:

  READY              every check passed, nothing waits for review
  READY_WITH_REVIEW  usable, but some cells are REVIEW_REQUIRED (listed in the 複查清單)
  INCOMPLETE         a channel failed, a column has no first cell, or a cell breaks the format
"""

from __future__ import annotations

from .evidence import REVIEW_REQUIRED
from .labeler import LabelRules, label_length

READY = "READY"
READY_WITH_REVIEW = "READY_WITH_REVIEW"
INCOMPLETE = "INCOMPLETE"


def validate_delivery(cells: dict[str, dict], records: list[dict], channels: list[str], start: str,
                      n_minutes: int, failed: dict[str, str] | None = None,
                      rules: LabelRules = LabelRules(), special: tuple[str, ...] = ("廣告", "氣象", "訊號中斷"),
                      sep: str = "/") -> dict:
    problems: list[str] = []
    failed = failed or {}
    h, m = int(start[:2]), int(start[2:])
    first = f"{h:02d}{m:02d}"
    last_total = h * 60 + m + n_minutes - 1
    last = f"{last_total // 60:02d}{last_total % 60:02d}"
    for ch in channels:
        if ch in failed:
            problems.append(f"{ch}：整台失敗（{failed[ch]}），欄位是空的")
            continue
        col = cells.get(ch) or {}
        # 規則 QA-2：每台第一分鐘一定要有值，否則前面那段是沒有說明的空白
        if first not in col:
            problems.append(f"{ch}：第一格 {first} 沒有值")
        for minute, cell in col.items():
            if not (first <= minute <= last):
                problems.append(f"{ch} {minute}：超出交付範圍 {first}–{last}")
            for item in str(cell.value).split(sep):
                if item in special:
                    continue
                if label_length(item) != rules.max_len:
                    problems.append(f"{ch} {minute}：「{item}」不是 {rules.max_len} 個字")
    rec_keys = {(r["channel"], r["minute"]) for r in records}
    for ch, col in cells.items():
        for minute in col:
            if (ch, minute) not in rec_keys:
                problems.append(f"{ch} {minute}：沒有判斷依據")
    pending = [r for r in records if (r.get("quality") or {}).get("status") == REVIEW_REQUIRED]
    status = INCOMPLETE if problems else (READY_WITH_REVIEW if pending else READY)
    return {
        "status": status,
        "range": f"{first}–{last}",
        "channels": channels,
        "cells": sum(len(c) for c in cells.values()),
        "review_required": len(pending),
        "auto_accepted": len(records) - len(pending),
        "problems": problems,
    }
