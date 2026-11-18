"""Cross-channel naming (spec X-1, X-2; FR-19).

When another channel already named the same event within ±window, reuse that
label — but only if every character of it is also backed by this channel's
own source text (X-2), so reuse can never import facts.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from .labeler import LabelSource, validate
from .schemas import Event, EventType

_CJK = re.compile(r"[一-鿿]{2,}")


def _terms(text: str) -> set[str]:
    out = set()
    for run in _CJK.findall(text or ""):
        out.update(run[i : i + 2] for i in range(len(run) - 1))
    return out


_GENERIC = {"今天", "台灣", "民眾", "記者", "表示", "不過", "因為", "所以", "一個", "我們", "他們", "這個", "沒有"}


def same_event(a: Event, b: Event, min_shared: int = 3, min_ratio: float = 0.35) -> bool:
    """Titles only: long speech transcripts share generic words with everything (11/06 run)."""
    ta, tb = a.source.bar_text or "", b.source.bar_text or ""
    if not ta or not tb:
        return False
    shared = (_terms(ta) & _terms(tb)) - _GENERIC
    ratio = SequenceMatcher(None, ta, tb).ratio()
    return len(shared) >= min_shared or (len(shared) >= 2 and ratio >= min_ratio)


def unify(events: list[Event], window_sec: int = 15 * 60) -> list[Event]:
    """Mutates labels in place; returns the events that were renamed."""
    news = sorted((e for e in events if e.type is EventType.NEWS), key=lambda e: e.start_sec)
    renamed = []
    for i, ev in enumerate(news):
        for prev in news[:i]:
            if prev.channel == ev.channel or ev.start_sec - prev.start_sec > window_sec:
                continue
            if prev.label == ev.label or not same_event(prev, ev):
                continue
            src = LabelSource([ev.source.bar_text] if ev.source.bar_text else [], "")
            if validate(prev.label, src):  # X-2: must be backed by this channel's own source
                continue
            ev.signals.setdefault("ids", []).append(f"X-1:{prev.channel}")
            ev.label = prev.label
            renamed.append(ev)
            break
    return renamed
