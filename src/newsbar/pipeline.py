"""Decision pipeline: observations → events → minute cells → sheet.

Extractors (OCR/ASR/visual) are not part of this skeleton; they will produce
SecondObs streams. Everything from SecondObs onward is implemented and tested.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from . import ad_state, cross_channel, evidence, excel_writer, labeler, segmenter, textfilter
from .minute_mapper import map_events
from .schemas import Event, EventType, Labels, SecondObs, Source


class PipelineError(RuntimeError):
    pass


def run_channel(
    channel: str,
    obs: list[SecondObs],
    generator: labeler.Generator | None = None,
    rules: dict | None = None,
    channel_reasons: list[str] | None = None,
) -> list[Event]:
    """channel_reasons: review reasons that apply to every cell of this channel (e.g. clock
    not read) — they make each event low confidence and show up in the review queue."""
    if textfilter.enabled(rules):
        obs = textfilter.filter_obs(obs)  # R7: strip LIVE/直播/溫度 … before anything reads the title
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
                           "low" if prev.reasons else seg.confidence,
                           Source(seg.bar_texts[0] if seg.bar_texts else None, seg.asr_text, seg.frame),
                           sigs, seg.fallback, list(prev.reasons))
            else:
                res = done[i]
                fb = ",".join(f for f in (seg.fallback, res.fallback) if f) or None
                ev = Event(channel, seg.start, EventType.NEWS, res.label, f"{channel}-{i}",
                           "low" if (fb or res.reasons) else seg.confidence,
                           Source(seg.bar_texts[0] if seg.bar_texts else None, seg.asr_text, seg.frame), sigs, fb,
                           list(res.reasons))
        else:
            ev = Event(channel, seg.start, seg.type, confidence=seg.confidence,
                       source=Source(seg.bar_texts[0] if seg.bar_texts else None, seg.asr_text, seg.frame),
                       signals=sigs, fallback=seg.fallback)
        for r in channel_reasons or []:
            if r not in ev.reasons:
                ev.reasons.append(r)
            ev.confidence = "low"
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
    channel_reasons: dict[str, list[str]] | None = None,
    prior_failed: dict[str, str] | None = None,
) -> dict:
    """One channel failing (segmenter, ad_state, labeling, mapping) is recorded in `failed` and the
    other channels still produce their column (R20). Only when no channel survives, or the workbook
    itself cannot be written, does the run stop — with a message that says why."""
    failed: dict[str, str] = dict(prior_failed or {})
    events: list[Event] = []
    by_ch: dict[str, list[Event]] = {}
    for ch, obs in streams.items():
        try:
            by_ch[ch] = run_channel(ch, obs, generator, rules, (channel_reasons or {}).get(ch))
        except Exception as e:  # noqa: BLE001
            failed[ch] = f"{type(e).__name__}: {e}"
        else:
            events += by_ch[ch]
    if not by_ch:
        raise PipelineError("沒有任何一台處理成功：" + "；".join(f"{c} {m}" for c, m in failed.items()))
    warnings: list[str] = []
    if ((rules or {}).get("cross_channel") or {}).get("enabled", True):
        try:
            cross_channel.unify(events, ((rules or {}).get("cross_channel") or {}).get("window_sec", 900))
        except Exception as e:  # noqa: BLE001 - a cosmetic cross-check must not cost the sheet
            warnings.append(f"跨台比對略過：{e}")
    labels = Labels.from_rules(rules)
    cells: dict[str, dict] = {}
    for ch in list(by_ch):
        try:
            cells[ch] = map_events(by_ch[ch], [ch], start, n_minutes, labels)[ch]
        except Exception as e:  # noqa: BLE001
            failed[ch] = f"{type(e).__name__}: {e}"
    if not cells:
        raise PipelineError("沒有任何一台產出分鐘格：" + "；".join(f"{c} {m}" for c, m in failed.items()))
    recs = evidence.build(cells, str((rules or {}).get("version", "0")))
    # Evidence first: if the workbook cannot be written the reviewer still gets the per-minute record.
    evidence.save(recs, out.with_suffix(".evidence.jsonl"), out.with_suffix(".report.csv"), failed)
    try:
        excel_writer.write(template, out, cells, {ch: channel_headers[ch] for ch in cells})
    except (excel_writer.WriterError, OSError, zipfile.BadZipFile) as e:
        raise PipelineError(f"無法寫入 Excel：{e}（範本沒有被改動；判斷依據已存為 {out.with_suffix('.evidence.jsonl').name}）") from e
    return {"events": [e for ch in cells for e in by_ch[ch]], "cells": cells, "evidence": recs,
            "failed": failed, "warnings": warnings}
