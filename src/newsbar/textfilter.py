"""Text-level filter for the main-bar OCR (R7): ROI coordinates keep most overlays out, but a
LIVE tag, a 獨家/最新 sticker or a corner temperature can still land inside the crop. Standalone
overlay tokens are removed before segmentation so they can neither become part of a title nor make
a title look "changed". The token list is config/lexicon.yaml → overlay_tokens / overlay_patterns.
"""

from __future__ import annotations

from dataclasses import replace

from . import lexicon
from .schemas import SecondObs


def filter_obs(obs: list[SecondObs], lex: lexicon.Lexicon | None = None) -> list[SecondObs]:
    """Copy of obs with overlay tokens stripped from bar_text (originals untouched)."""
    lex = lex or lexicon.get()
    out = []
    for o in obs:
        cleaned = lex.strip_overlay(o.bar_text)
        out.append(o if cleaned == o.bar_text else replace(o, bar_text=cleaned))
    return out


def enabled(rules: dict | None) -> bool:
    return bool(((rules or {}).get("text_filter") or {}).get("enabled", True))
