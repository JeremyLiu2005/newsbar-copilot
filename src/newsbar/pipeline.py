"""Decision pipeline: observations → events → minute cells → sheet.

Extractors (OCR/ASR/visual) are not part of this skeleton; they will produce
SecondObs streams. Everything from SecondObs onward is implemented and tested.
"""

from __future__ import annotations

from pathlib import Path

from . import ad_state, cross_channel, evidence, excel_writer, labeler, segmenter
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
    lab = (rules or {}).get("labeler") or {}
    retries = lab.get("retries", 1)
    # Label all new stories up front, several LLM calls at a time (Ollama serves them in parallel).
    todo = {i: labeler.LabelSource(s.bar_texts, s.asr_text) for i, s in enumerate(segs)
            if s.type is EventType.NEWS and s.reuse_of is None}
    workers = max(1, int(lab.get("parallel", 4))) if generator is not None else 1
    if workers > 1 and len(todo) > 1:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=workers) as ex:
            done = dict(zip(todo, ex.map(lambda src: labeler.make_label(src, generator, retries), todo.values())))
    else:
        done = {i: labeler.make_label(src, generator, retries) for i, src in todo.items()}
    for i, seg in enumerate(segs):
        sigs = {"ids": seg.signals}
        if seg.type is EventType.NEWS:
            if seg.reuse_of is not None and seg.reuse_of in seg_event:
                prev = seg_event[seg.reuse_of]
                ev = Event(channel, seg.start, EventType.NEWS, prev.label, prev.story_id,
                           seg.confidence, Source(seg.bar_texts[0] if seg.bar_texts else None, seg.asr_text, seg.frame),
                           sigs, seg.fallback)
            else:
                res = done[i]
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
    if ((rules or {}).get("cross_channel") or {}).get("enabled", True):
        cross_channel.unify(events, ((rules or {}).get("cross_channel") or {}).get("window_sec", 900))
    cells = map_events(events, channel_headers.keys(), start, n_minutes, Labels.from_rules(rules))
    excel_writer.write(template, out, cells, channel_headers)
    recs = evidence.build(cells, str((rules or {}).get("version", "0")))
    evidence.save(recs, out.with_suffix(".evidence.jsonl"), out.with_suffix(".report.csv"))
    return {"events": events, "cells": cells, "evidence": recs}
