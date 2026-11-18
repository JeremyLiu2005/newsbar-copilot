"""Decision pipeline: observations → events → minute cells → sheet.

Extractors (OCR/ASR/visual) are not part of this skeleton; they will produce
SecondObs streams. Everything from SecondObs onward is implemented and tested.
"""

from __future__ import annotations

from pathlib import Path

from . import ad_state, evidence, excel_writer, labeler, segmenter
from .minute_mapper import map_events
from .schemas import Event, EventType, Labels, SecondObs, Source


def run_channel(
    channel: str,
    obs: list[SecondObs],
    generator: labeler.Generator | None = None,
    rules: dict | None = None,
) -> list[Event]:
    ad = ad_state.detect(obs, ad_state.AdParams.from_rules(rules))
    segs = segmenter.segment(obs, ad, segmenter.SegParams.from_rules(rules))
    events: list[Event] = []
    seg_event: dict[int, Event] = {}
    retries = ((rules or {}).get("labeler") or {}).get("retries", 1)
    for i, seg in enumerate(segs):
        sigs = {"ids": seg.signals}
        if seg.type is EventType.NEWS:
            if seg.reuse_of is not None and seg.reuse_of in seg_event:
                prev = seg_event[seg.reuse_of]
                ev = Event(channel, seg.start, EventType.NEWS, prev.label, prev.story_id,
                           seg.confidence, Source(seg.bar_texts[0] if seg.bar_texts else None, seg.asr_text, seg.frame),
                           sigs, seg.fallback)
            else:
                src = labeler.LabelSource(seg.bar_texts, seg.asr_text)
                res = labeler.make_label(src, generator, retries)
                fb = ",".join(f for f in (seg.fallback, res.fallback) if f) or None
                ev = Event(channel, seg.start, EventType.NEWS, res.label, f"{channel}-{i}",
                           "low" if fb else seg.confidence,
                           Source(seg.bar_texts[0] if seg.bar_texts else None, seg.asr_text, seg.frame), sigs, fb)
        else:
            ev = Event(channel, seg.start, seg.type, confidence=seg.confidence,
                       source=Source(seg.bar_texts[0] if seg.bar_texts else None, seg.asr_text, seg.frame),
                       signals=sigs, fallback=seg.fallback)
        seg_event[i] = ev
        events.append(ev)
    return events


def run(
    streams: dict[str, list[SecondObs]],
    template: Path,
    out: Path,
    channel_headers: dict[str, str],
    generator: labeler.Generator | None = None,
    rules: dict | None = None,
    start: str = "1200",
    n_minutes: int = 60,
) -> dict:
    events = [ev for ch, obs in streams.items() for ev in run_channel(ch, obs, generator, rules)]
    cells = map_events(events, channel_headers.keys(), start, n_minutes, Labels.from_rules(rules))
    excel_writer.write(template, out, cells, channel_headers)
    recs = evidence.build(cells, str((rules or {}).get("version", "0")))
    evidence.save(recs, out.with_suffix(".evidence.jsonl"), out.with_suffix(".report.csv"))
    return {"events": events, "cells": cells, "evidence": recs}
