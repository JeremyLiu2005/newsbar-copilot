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


def same_event(a: Event, b: Event, min_shared: int = 2, min_ratio: float = 0.35) -> bool:
    ta = (a.source.bar_text or "") + " " + (a.source.asr_text or "")
    tb = (b.source.bar_text or "") + " " + (b.source.asr_text or "")
    shared = _terms(ta) & _terms(tb)
    ratio = SequenceMatcher(None, a.source.bar_text or "", b.source.bar_text or "").ratio()
    return len(shared) >= min_shared and (ratio >= min_ratio or len(shared) >= 4)


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
            src = LabelSource([ev.source.bar_text] if ev.source.bar_text else [], ev.source.asr_text or "")
            if validate(prev.label, src):  # X-2: must be backed by this channel's own source
                continue
            ev.signals.setdefault("ids", []).append(f"X-1:{prev.channel}")
            ev.label = prev.label
            renamed.append(ev)
            break
    return renamed
