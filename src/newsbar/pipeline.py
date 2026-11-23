"""Decision pipeline: observations → events → minute cells → sheet.

Extractors (OCR/ASR/visual) are not part of this skeleton; they will produce
SecondObs streams. Everything from SecondObs onward is implemented and tested.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from . import ad_state, cross_channel, delivery, evidence, excel_writer, labeler, ocr_check, segmenter, textfilter, vision
from .minute_mapper import map_events
from .schemas import Event, EventType, Labels, SecondObs, Source


DEFAULT_PARALLEL = 4  # 同時送出的 AI 請求數


class PipelineError(RuntimeError):
    pass


# 規則 TXT-10：標題的文字辨識信心低（Apple Vision 的繁中信心只有 0.3／0.5／1.0 三級；0.3 幾乎都是讀壞的標題，
# 11/06：專冢澬金收芋、競緦 挨 蟲 沒磑貌、昔曰龍頭）→ 這一則列入複查。0.5 是多數台的正常值，不算。
OCR_REVIEW_CONF = 0.4
REASON_OCR = "標題可能讀錯（文字辨識信心低）"


def title_confidence(obs: list[SecondObs]) -> dict[str, float]:
    """Median OCR confidence of each title over the seconds it was on screen."""
    by: dict[str, list[float]] = {}
    for o in obs:
        if o.bar_text:
            by.setdefault(o.bar_text, []).append(o.bar_conf)
    return {t: sorted(cs)[len(cs) // 2] for t, cs in by.items()}


# 規則 REV-1：換題／擷取層的旗標對應到哪一級
SEG_FALLBACK_NOTES = {"FB-1": (labeler.LEVEL_INFO, "換題訊號偏弱（只有標題佐證）"),
                      "FB-3": (labeler.LEVEL_FAIL, "標題讀不到，靠語音定名")}


def _story_obs(obs: list[SecondObs], start: float, end: float | None) -> list[SecondObs]:
    hi = end if end is not None else start + 1
    return [o for o in obs if start <= o.sec < hi and o.bar_text]


def decide_story(channel: str, bar_texts: list[str], asr_text: str, obs: list[SecondObs],
                 generator: labeler.Generator | None, rules: dict | None,
                 conf_of: dict[str, float] | None = None, freq=None,
                 vision_backend: vision.VisionBackend | None = None, vision_cache: vision.ReadCache | None = None) -> dict:
    """One story: OCR check → (optional cloud re-read of low-confidence titles) → short title → review notes.
    Also used by the regression replay."""
    lab = (rules or {}).get("labeler") or {}
    lrules = labeler.LabelRules.from_rules(rules)
    titles, ocr_recs, avoid = ocr_check.check_story(list(bar_texts), obs, asr_text, ocr_check.confusions_from(rules), freq)
    vrecs: list[dict] = []
    if vision_backend is not None:
        # 規則 VIS-1（選用，預設關閉）：信心低的標題交雲端視覺模型重讀；只在模型保留了 OCR 大部分字時才採用
        titles, fixed, vrecs = vision.reread_titles(titles, conf_of, obs, vision_backend, rules, vision_cache)
        if fixed:
            keep = [r for k, r in enumerate(ocr_recs) if k not in fixed]
            avoid = {s["text"] for r in keep for s in r["suspects"]}
            avoid |= {ch for r in keep for s in r["suspects"] if s.get("pattern") for ch in s["text"]}
            conf_of = dict(conf_of or {})
            for k in fixed:  # a title confirmed by a second reader is no longer a low-confidence read
                conf_of[ocr_recs[k]["ocr_raw"]] = 1.0
            for k in fixed:
                ocr_recs[k] = {**ocr_recs[k], "suspects": []}
    res = labeler.make_label(labeler.LabelSource(titles, asr_text), generator,
                             lab.get("retries", labeler.DEFAULT_RETRIES), lrules, avoid)
    d = _story_result(res, titles, ocr_recs, rules, conf_of)
    for v in vrecs:
        if v["status"] == "replaced":
            d["notes"].append({"level": labeler.LEVEL_INFO, "reason": "標題經雲端模型重讀",
                               "detail": f"{v['ocr']} → {v['model']}（字元重疊 {v['overlap']}）"})
    d["review_level"] = labeler.top_level(d["notes"])
    d["vision"] = vrecs
    return d


def _story_result(res: labeler.LabelResult, titles: list[str], ocr_recs: list[dict], rules: dict | None,
                  conf_of: dict[str, float] | None) -> dict:
    notes = list(res.notes)
    for r in ocr_recs:
        if r["review_status"] == ocr_check.STATUS_FIXED:
            notes.append({"level": labeler.LEVEL_INFO, "reason": "標題 OCR 已依證據修正",
                          "detail": f"{r['ocr_raw']} → {r['ocr_corrected']}（{r['correction_reason']}）"})
        for sp in r["suspects"]:
            used = sp["text"] in (res.label or "")
            notes.append({"level": labeler.LEVEL_CHECK if used else labeler.LEVEL_INFO,
                          "reason": "標題有可疑字" + ("（短標有用到）" if used else "（未用於短標）"),
                          "detail": f"「{sp['text']}」{('疑為「' + sp['suggestion'] + '」') if sp['suggestion'] else ''}：{sp['why']}"})
    ocr_review = float(((rules or {}).get("ocr") or {}).get("review_conf", OCR_REVIEW_CONF))
    if conf_of and any(conf_of.get(r["ocr_raw"], 1.0) <= ocr_review for r in ocr_recs[:2]):
        notes.append({"level": labeler.LEVEL_CHECK, "reason": REASON_OCR})
    return {"label": res.label, "status": res.status, "fallback": res.fallback, "notes": notes,
            "review_level": labeler.top_level(notes), "ocr": ocr_recs, "titles": titles, "result": res}


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
        # 規則 TXT-6：先刪掉 LIVE、直播、角落溫度等疊字，再讀標題
        obs = textfilter.filter_obs(obs)  # R7: strip LIVE/直播/溫度 … before anything reads the title
    # 規則 AD-1、AD-11、FB-6：先判斷廣告與訊號中斷（廣告期間的文字不採用：AD-5）
    ad = ad_state.detect(obs, ad_state.AdParams.from_rules(rules))
    # 規則 NEW-1、NEW-2a、SAME-3、TIME-1：再依主標題與佐證切出每一則新聞
    segs = segmenter.segment(obs, ad, segmenter.SegParams.from_rules(rules))
    conf_of = title_confidence(obs)
    events: list[Event] = []
    seg_event: dict[int, Event] = {}
    lab = (rules or {}).get("labeler") or {}
    # Label all new stories up front, several LLM calls at a time (Ollama serves them in parallel).
    todo = {i: s for i, s in enumerate(segs) if s.type is EventType.NEWS and s.reuse_of is None}

    freq = ocr_check.char_titles(o.bar_text for o in obs)  # the channel's own OCR as the rarity reference
    vb = vision.backend_from_rules(rules)  # None unless vision.mode (or NEWSBAR_VISION_MODE) turns it on
    vcfg = (rules or {}).get("vision") or {}
    vcache = vision.ReadCache(Path(vcfg["cache"])) if vb is not None and vcfg.get("cache") else None

    def _one(seg):
        return decide_story(channel, seg.bar_texts, seg.asr_text, _story_obs(obs, seg.start, seg.end), generator,
                            rules, conf_of, freq, vb, vcache)

    workers = max(1, int(lab.get("parallel", DEFAULT_PARALLEL))) if generator is not None else 1
    if workers > 1 and len(todo) > 1:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=workers) as ex:
            done = dict(zip(todo, ex.map(_one, todo.values())))
    else:
        done = {i: _one(seg) for i, seg in todo.items()}
    # 規則 R20-4：這一台寫完後，因「AI 服務無回應」改用規則備援的則數，等服務恢復再補問一次（例如模型剛載入、
    # 一時忙碌而讓斷路器跳開）。先放一個探測請求；仍然沒回應就停止，保留規則備援，不拖慢整次執行。
    retry = [i for i, d in done.items() if any(n.get("reason") == labeler.REASON_LLM_DOWN for n in d["notes"])]
    if retry and generator is not None:
        allow = getattr(generator, "allow_probe", None)
        for i in retry:
            if allow:
                allow()
            d2 = _one(todo[i])
            if any(n.get("reason") == labeler.REASON_LLM_DOWN for n in d2["notes"]):
                break  # still down: keep the rule labels for the rest
            done[i] = d2
    for i, seg in enumerate(segs):
        sigs = {"ids": seg.signals, "boundary": seg.boundary, "end_sec": seg.end}
        src = Source(seg.bar_texts[0] if seg.bar_texts else None, seg.asr_text, seg.frame)
        seg_notes = [{"level": lv, "reason": why} for code, (lv, why) in SEG_FALLBACK_NOTES.items()
                     if code in (seg.fallback or "").split(",")]
        b = seg.boundary or {}
        if b.get("review_required"):
            seg_notes.append({"level": labeler.LEVEL_INFO, "reason": "換題佐證不足",
                              "detail": "、".join(b.get("reason_codes", []))})
        if seg.type is EventType.NEWS:
            if seg.reuse_of is not None and seg.reuse_of in seg_event:
                prev = seg_event[seg.reuse_of]
                notes = [n for n in prev.notes] + seg_notes
                ev = Event(channel, seg.start, EventType.NEWS, prev.label, prev.story_id,
                           "low" if labeler.top_level(notes) in (labeler.LEVEL_CHECK, labeler.LEVEL_FAIL) else seg.confidence,
                           src, sigs, seg.fallback, list(prev.reasons), notes, list(prev.ocr))
            else:
                d = done[i]
                fb = ",".join(f for f in (seg.fallback, d["fallback"]) if f) or None
                notes = d["notes"] + seg_notes
                ev = Event(channel, seg.start, EventType.NEWS, d["label"], f"{channel}-{i}",
                           "low" if labeler.top_level(notes) in (labeler.LEVEL_CHECK, labeler.LEVEL_FAIL) else seg.confidence,
                           src, sigs, fb, list(dict.fromkeys(n["reason"] for n in notes)), notes, d["ocr"])
        else:
            ev = Event(channel, seg.start, seg.type, confidence=seg.confidence, source=src, signals=sigs,
                       fallback=seg.fallback, notes=seg_notes)
        for r in channel_reasons or []:
            if r not in ev.reasons:
                ev.reasons.append(r)
                ev.notes.append({"level": labeler.LEVEL_FAIL, "reason": r})
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
    run_id: str | None = None,
    channels: list[str] | None = None,
) -> dict:
    """One channel failing (segmenter, ad_state, labeling, mapping) is recorded in `failed` and the
    other channels still produce their column (R20). Only when no channel survives, or the workbook
    itself cannot be written, does the run stop — with a message that says why."""
    failed: dict[str, str] = dict(prior_failed or {})
    events: list[Event] = []
    by_ch: dict[str, list[Event]] = {}
    for ch, obs in streams.items():
        try:
            # 規則 MAP-1：四台各自獨立判斷；規則 R20-1：一台出錯只記錄，不影響其他台
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
            # 規則 X-1：他台 15 分鐘內已替同一事件命名 → 沿用（仍須通過本台來源檢查 X-2）
            cross_channel.unify(events, ((rules or {}).get("cross_channel") or {}).get("window_sec", cross_channel.DEFAULT_WINDOW_SEC))
        except Exception as e:  # noqa: BLE001 - a cosmetic cross-check must not cost the sheet
            warnings.append(f"跨台比對略過：{e}")
    labels = Labels.from_rules(rules)
    cells: dict[str, dict] = {}
    for ch in list(by_ch):
        try:
            # 規則 MAP-3、MAP-7、OUT-2：把秒級事件對準到分鐘格
            cells[ch] = map_events(by_ch[ch], [ch], start, n_minutes, labels)[ch]
        except Exception as e:  # noqa: BLE001
            failed[ch] = f"{type(e).__name__}: {e}"
    if not cells:
        raise PipelineError("沒有任何一台產出分鐘格：" + "；".join(f"{c} {m}" for c, m in failed.items()))
    # 規則 R20-3：每一格的依據先存檔，Excel 寫失敗也留得下複查資料
    recs = evidence.build(cells, str((rules or {}).get("version", "0")), run_id)
    # Evidence first: if the workbook cannot be written the reviewer still gets the per-minute record.
    evidence.save(recs, out.with_suffix(".evidence.jsonl"), out.with_suffix(".report.csv"), failed)
    # 規則 EVD-1：每個換題點的機率、證據與是否需複核，另存一份（一則新聞一行）
    evidence.save_stories({ch: by_ch[ch] for ch in cells}, out.with_suffix(".stories.jsonl"), run_id)
    # 規則 QA-2：交付前檢查（範圍、每台第一格、字數、依據齊全、待複查數），只說明狀態，不擋寫入
    lab = labeler.LabelRules.from_rules(rules)
    deliv = delivery.validate_delivery(cells, recs, channels or list(channel_headers), start, n_minutes, failed, lab,
                                       (labels.ad, labels.weather, labels.outage), labels.multi_sep)
    try:
        excel_writer.write(template, out, cells, {ch: channel_headers[ch] for ch in cells})
    except (excel_writer.WriterError, OSError, zipfile.BadZipFile) as e:
        raise PipelineError(f"無法寫入 Excel：{e}（範本沒有被改動；判斷依據已存為 {out.with_suffix('.evidence.jsonl').name}）") from e
    return {"events": [e for ch in cells for e in by_ch[ch]], "cells": cells, "evidence": recs,
            "failed": failed, "warnings": warnings, "delivery": deliv}
