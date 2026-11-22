"""ML for the two decisions the rules make (story change, ad state). Default OFF.

    newsbar ml dataset   rebuild change candidates + per-second ad rows with gold labels (data/ml/*.jsonl)
    newsbar ml train     logistic regression → data/models/{change,ad}_vN.json (refuses to replace a better active model)
    newsbar ml eval      rules vs learned on the held-out window, same evaluate(), plus the learned weights
    newsbar ml cv        block cross-validation (the only out-of-sample ad evidence when the train window has no break)
    newsbar ml rollback  reactivate the previous model

Labels come from the gold JSON (eval/gold/*.json: {channel, sec, kind, label}); sec is relative to the sheet start.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import numpy as np
import yaml

from . import ad_state, annotate, evaluate, extract, mlmodel, pipeline, segmenter, textfilter
from .minute_mapper import map_events

SHEET_START = "1200"
LABEL_TOL_SEC = 60  # a candidate within ±60 s of a gold change start (nearest, 1:1) is the positive


def hhmm_to_sec(hhmm: str, sheet_start: str = SHEET_START) -> int:
    return (int(hhmm[:2]) * 60 + int(hhmm[2:]) - int(sheet_start[:2]) * 60 - int(sheet_start[2:])) * 60


def load_gold(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def gold_changes(gold: dict, channel: str) -> list[int]:
    return sorted(int(i["sec"]) for i in gold["items"] if i["channel"] == channel and i["kind"] == "news")


def gold_ad_intervals(gold: dict, channel: str, end_of_data: int = 10 ** 9) -> list[tuple[int, int]]:
    """An ad runs from its start to the next gold item of the same channel (the sheet notation:
    blank continues the ad until the next entry)."""
    items = sorted((i for i in gold["items"] if i["channel"] == channel), key=lambda i: i["sec"])
    out = []
    for k, it in enumerate(items):
        if it["kind"] == "ad":
            out.append((int(it["sec"]), int(items[k + 1]["sec"]) if k + 1 < len(items) else end_of_data))
    return out


def rules_only(rules: dict | None) -> dict:
    """Same rules with both detectors forced back to the rule path (dataset building, baselines)."""
    r = copy.deepcopy(rules or {})
    r.setdefault("segmenter", {})["mode"] = "rules"
    r.setdefault("ad_state", {})["mode"] = "rules"
    return r


def with_models(rules: dict | None, change: str | None = None, ad: str | None = None) -> dict:
    r = rules_only(rules)
    if change:
        r["segmenter"].update(mode="learned", model=change)
    if ad:
        r["ad_state"].update(mode="learned", model=ad)
    return r


def prepared(obs, rules):
    return textfilter.filter_obs(obs) if textfilter.enabled(rules) else obs


def match_positive(cands: list[int], gold_secs: list[int], tol: int = LABEL_TOL_SEC) -> set[int]:
    """Greedy nearest 1:1 matching; returns indexes of candidates that carry a gold change."""
    pairs = sorted((abs(c - g), ci, gi) for ci, c in enumerate(cands) for gi, g in enumerate(gold_secs) if abs(c - g) <= tol)
    uc, ug, pos = set(), set(), set()
    for _, ci, gi in pairs:
        if ci in uc or gi in ug:
            continue
        uc.add(ci)
        ug.add(gi)
        pos.add(ci)
    return pos


def change_rows(channel: str, obs, rules: dict | None, gold: dict) -> list[dict]:
    rr = rules_only(rules)
    obs = prepared(obs, rr)
    ad = ad_state.detect(obs, ad_state.AdParams.from_rules(rr))
    rec: list[dict] = []
    segmenter.segment(obs, ad, segmenter.SegParams.from_rules(rr), recorder=rec)
    pos = match_positive([r["sec"] for r in rec], gold_changes(gold, channel))
    return [{"channel": channel, "sec": r["sec"], "features": r["features"], "label": int(i in pos),
             "rules_new": bool(r.get("rules_new"))} for i, r in enumerate(rec)]


def ad_rows(channel: str, obs, rules: dict | None, gold: dict) -> list[dict]:
    rr = rules_only(rules)
    obs = prepared(obs, rr)
    iv = gold_ad_intervals(gold, channel)
    return [{"channel": channel, "sec": sec, "features": f, "label": int(any(a <= sec < b for a, b in iv))}
            for sec, f in ad_state.ad_feature_rows(obs)]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]


def gold_channels(gold: dict) -> list[str]:
    return sorted({i["channel"] for i in gold["items"]})


def load_streams(workdir: Path, channels: list[str]) -> dict:
    return {ch: extract.load_obs(Path(workdir) / ch / "obs.jsonl") for ch in channels
            if (Path(workdir) / ch / "obs.jsonl").exists()}


def build_dataset(streams: dict, rules: dict | None, gold: dict, out_dir: Path) -> dict:
    ch_rows, ad_r = [], []
    for ch, obs in streams.items():
        ch_rows += change_rows(ch, obs, rules, gold)
        ad_r += ad_rows(ch, obs, rules, gold)
    write_jsonl(out_dir / "change.jsonl", ch_rows)
    write_jsonl(out_dir / "ad.jsonl", ad_r)
    return {"change": {"rows": len(ch_rows), "positives": sum(r["label"] for r in ch_rows)},
            "ad": {"rows": len(ad_r), "positives": sum(r["label"] for r in ad_r)}}


# ---- scoring through the real pipeline --------------------------------------------------------
def system_col(channel: str, obs, rules: dict | None) -> dict[str, str]:
    evs = pipeline.run_channel(channel, obs, None, rules)
    cells = map_events(evs, [channel], SHEET_START, 60)[channel]
    return {m: c.value for m, c in cells.items() if c.value}


def gold_col(gold: dict, channel: str) -> dict[str, str]:
    cells = annotate.gold_cells([i for i in gold["items"] if i["channel"] == channel], [channel], SHEET_START, 60)[channel]
    return {m: c.value for m, c in cells.items() if c.value}


def score(streams: dict, gold: dict, rules: dict | None, window: tuple[str, str], tolerance: int = 1) -> evaluate.Report:
    chans = [c for c in gold_channels(gold) if c in streams]
    system = {c: system_col(c, streams[c], rules) for c in chans}
    gcols = {c: gold_col(gold, c) for c in chans}
    return evaluate.evaluate(system, gcols, SHEET_START, 60, tolerance, window)


def _fmt(d: dict) -> str:
    return f"P {d['precision']:.3f}  R {d['recall']:.3f}  F1 {d['f1']:.3f}  (tp {d['tp']} fp {d['fp']} fn {d['fn']})"


def _active_path(kind: str, models_dir: str) -> str | None:
    return mlmodel.read_registry(models_dir).get(kind)


# ---- commands ---------------------------------------------------------------------------------
def _ctx(a):
    rules = yaml.safe_load(Path(a.rules).read_text(encoding="utf-8")) if a.rules and Path(a.rules).exists() else {}
    gold = load_gold(Path(a.gold))
    streams = load_streams(Path(a.workdir), gold_channels(gold))
    if not streams:
        raise SystemExit(f"找不到 obs.jsonl（{a.workdir}/<台別>/obs.jsonl）")
    return rules, gold, streams


def cmd_dataset(a) -> int:
    rules, gold, streams = _ctx(a)
    info = build_dataset(streams, rules, gold, Path(a.ml_dir))
    for k, v in info.items():
        print(f"{k}: {v['rows']} 列，正例 {v['positives']} → {Path(a.ml_dir) / (k + '.jsonl')}")
    return 0


def _rows_for(a, kind: str) -> list[dict]:
    p = Path(a.ml_dir) / f"{kind}.jsonl"
    if not p.exists() or getattr(a, "rebuild", False):
        rules, gold, streams = _ctx(a)
        build_dataset(streams, rules, gold, Path(a.ml_dir))
    return read_jsonl(p)


KINDS = {"change": segmenter.CHANGE_FEATURES, "ad": list(ad_state.AD_FEATURES)}


def _heldout_f1(kind: str, model_path: str, streams, gold, rules, window) -> tuple[float, dict]:
    kw = {"change" if kind == "change" else "ad": model_path}
    rep = score(streams, gold, with_models(rules, **kw), window)
    d = rep.change if kind == "change" else rep.ad
    return d["f1"], d


def cmd_train(a) -> int:
    rules, gold, streams = _ctx(a)
    cutoff = hhmm_to_sec(a.train_until)
    window = (a.eval_from, a.eval_to)
    base = score(streams, gold, rules_only(rules), window)
    status = 0
    for kind in (["change", "ad"] if a.kind == "both" else [a.kind]):
        rows = [r for r in _rows_for(a, kind) if r["sec"] < cutoff]
        pos = sum(r["label"] for r in rows)
        print(f"[{kind}] 訓練窗 sec<{cutoff}（{a.train_until} 之前）：{len(rows)} 列，正例 {pos}")
        try:
            m = mlmodel.train_model(kind, KINDS[kind], rows, {"until": a.train_until, "sheet_start": SHEET_START,
                                                              "channels": gold_channels(gold)}, a.l2)
        except ValueError as e:
            print(f"[{kind}] 不訓練：{e}")
            status = status or 0
            continue
        path = mlmodel.save_model(m, a.models_dir)
        f1, d = _heldout_f1(kind, str(path), streams, gold, rules, window)
        m.metrics["heldout"] = {"window": list(window), **d}
        path.write_text(json.dumps(m.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
        rules_d = base.change if kind == "change" else base.ad
        print(f"[{kind}] 存成 {path}；訓練 F1 {m.metrics['train']['f1']}；留出窗 {window[0]}–{window[1]} F1 {f1}（規則 {rules_d['f1']}）")
        cur = _active_path(kind, a.models_dir)
        if cur and not a.force:
            cur_f1, _ = _heldout_f1(kind, cur, streams, gold, rules, window)
            if f1 < cur_f1:
                print(f"[{kind}] 拒絕取代 active（{cur}）：新模型留出 F1 {f1} < 現行 {cur_f1}；舊模型維持啟用。--force 可強制")
                continue
        mlmodel.set_active(kind, path, a.models_dir)
        print(f"[{kind}] active → {path}")
    return status


def cmd_eval(a) -> int:
    rules, gold, streams = _ctx(a)
    window = (a.eval_from, a.eval_to)
    ch_m = a.change_model or _active_path("change", a.models_dir)
    ad_m = a.ad_model or _active_path("ad", a.models_dir)
    base = score(streams, gold, rules_only(rules), window, a.tolerance)
    learned = score(streams, gold, with_models(rules, ch_m, ad_m), window, a.tolerance)
    print(f"留出窗 {window[0]}–{window[1]}，容許 ±{a.tolerance} 分，台別 {gold_channels(gold)}（人工初標，非獨立標註）")
    print(f"換題  規則  : {_fmt(base.change)}")
    print(f"換題  學習  : {_fmt(learned.change)}   模型 {ch_m or '（無，等同規則）'}")
    print(f"廣告  規則  : {_fmt(base.ad)}")
    print(f"廣告  學習  : {_fmt(learned.ad)}   模型 {ad_m or '（無，等同規則）'}")
    for kind, path in (("change", ch_m), ("ad", ad_m)):
        if path:
            m = mlmodel.load_model(path, kind)
            print(f"\n各訊號權重（{kind}，標準化後；正=推向「{'換題' if kind == 'change' else '廣告'}」，門檻 {m.threshold:.3f}）")
            for name, w in m.weight_table():
                print(f"  {name:14s}{w:+.3f}")
    return 0


def _blocks() -> list[tuple[int, int]]:
    """Time blocks that each hold one TVBS ad break (gold: 2250–2474, 2800–3031, 3241–3402)."""
    return [(0, 2500), (2500, 3100), (3100, 3600)]


def cmd_cv(a) -> int:
    """Train on the other blocks, score the held-out block through the real pipeline. Reports the
    pooled counts for rules vs learned. Tiny data: read the counts, not just the F1."""
    rules, gold, streams = _ctx(a)
    tot = {k: {"rules": evaluate.PRF(), "learned": evaluate.PRF()} for k in ("change", "ad")}
    for kind in (["change", "ad"] if a.kind == "both" else [a.kind]):
        allrows = _rows_for(a, kind)
        for lo, hi in _blocks():
            tr = [r for r in allrows if not (lo <= r["sec"] < hi)]
            win = (f"{12 + (lo // 60) // 60:02d}{(lo // 60) % 60:02d}", f"{12 + ((hi - 1) // 60) // 60:02d}{((hi - 1) // 60) % 60:02d}")
            try:
                m = mlmodel.train_model(kind, KINDS[kind], tr, {"cv_holdout_sec": [lo, hi]}, a.l2)
            except ValueError as e:
                print(f"[{kind}] 區塊 {lo}-{hi}：{e}")
                continue
            tmp = Path(a.models_dir) / "_cv"
            p = mlmodel.save_model(m, tmp)
            rb = score(streams, gold, rules_only(rules), win)
            rl = score(streams, gold, with_models(rules, **{kind: str(p)}), win)
            for name, rep in (("rules", rb), ("learned", rl)):
                d = rep.change if kind == "change" else rep.ad
                x = tot[kind][name]
                x.tp, x.fp, x.fn = x.tp + d["tp"], x.fp + d["fp"], x.fn + d["fn"]
            d1, d2 = (rb.change, rl.change) if kind == "change" else (rb.ad, rl.ad)
            print(f"[{kind}] 留出 {win[0]}–{win[1]}  規則 F1 {d1['f1']} (tp{d1['tp']} fp{d1['fp']} fn{d1['fn']})  學習 F1 {d2['f1']} (tp{d2['tp']} fp{d2['fp']} fn{d2['fn']})  訓練正例 {m.metrics['train']['positives']}")
    for kind in tot:
        r, l = tot[kind]["rules"], tot[kind]["learned"]
        if r.tp + r.fp + r.fn or l.tp + l.fp + l.fn:
            print(f"[{kind}] 合計  規則 {r.as_dict()}\n         學習 {l.as_dict()}")
    return 0


def cmd_rollback(a) -> int:
    for kind in (["change", "ad"] if a.kind == "both" else [a.kind]):
        prev = mlmodel.rollback(kind, a.models_dir)
        print(f"[{kind}] 退回 {prev}" if prev else f"[{kind}] 沒有更早的版本可退")
    return 0
