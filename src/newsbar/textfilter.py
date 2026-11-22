"""Text-level filter for the main-bar OCR (R7): ROI coordinates keep most overlays out, but a
LIVE tag, a 獨家/最新 sticker or a corner temperature can still land inside the crop. Standalone
overlay tokens are removed before segmentation so they can neither become part of a title nor make
a title look "changed". The token list is config/lexicon.yaml → overlay_tokens / overlay_patterns.
"""

from __future__ import annotations

import re
from dataclasses import replace

from . import lexicon
from .schemas import SecondObs

# 規則 TXT-8：夾在兩個中文字之間的單一 I、l、|、1 幾乎都是驚嘆號被 OCR 讀錯（史詩崩跌I台股殺3737點）
_BANG = re.compile(r"(?<=[\u4e00-\u9fff])[Il|](?=[\u4e00-\u9fff])")


def fix_ocr_marks(text: str | None) -> str | None:
    """A lone I / l / | between two Chinese characters is an OCR misread of ！ (11/06 TVBS)."""
    return _BANG.sub("!", text) if text else text


def filter_obs(obs: list[SecondObs], lex: lexicon.Lexicon | None = None) -> list[SecondObs]:
    """Copy of obs with overlay tokens stripped and OCR punctuation misreads fixed in bar_text
    (originals untouched)."""
    lex = lex or lexicon.get()
    out = []
    for o in obs:
        cleaned = fix_ocr_marks(lex.strip_overlay(o.bar_text))
        out.append(o if cleaned == o.bar_text else replace(o, bar_text=cleaned))
    return out


def enabled(rules: dict | None) -> bool:
    return bool(((rules or {}).get("text_filter") or {}).get("enabled", True))
