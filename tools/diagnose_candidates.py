"""Read-only change-candidate diagnosis (規格 §4): every stable new title the segmenter considered,
whether it opened a story, and which rule kept it in the running story. Decisions are NOT changed —
the segmenter runs with its normal parameters and only its `recorder` hook is used.

  uv run python tools/diagnose_candidates.py --work /abs/data/work_rerun_1122_asr --out /abs/out/stageA_new/12監看_20261106.candidates.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import yaml  # noqa: E402

from newsbar import ad_state, extract, lexicon, segmenter, textfilter  # noqa: E402

CHANNELS = ["tvbs", "ettv", "ftv", "cts"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--rules", default=str(ROOT / "config/rules.1106.yaml"))
    a = ap.parse_args()
    rules = yaml.safe_load(Path(a.rules).read_text(encoding="utf-8"))
    lexicon.use(str(ROOT / "config/lexicon.yaml"))
    judge = segmenter.LexicalJudge()
    out = []
    gaps = []
    for ch in CHANNELS:
        obs = extract.load_obs(Path(a.work) / ch / "obs.jsonl")
        if textfilter.enabled(rules):
            obs = textfilter.filter_obs(obs)
        ad = ad_state.detect(obs, ad_state.AdParams.from_rules(rules))
        rec: list = []
        segs = segmenter.segment(obs, ad, segmenter.SegParams.from_rules(rules), recorder=rec)
        text_at = {}
        for o in obs:
            text_at.setdefault(o.sec, o.bar_text)
        asr_at = {o.sec: o.asr_text for o in obs if o.asr_text}
        for r in rec:
            t = r["sec"]
            story = next((s for s in segs if s.start <= t < (s.end or 10**9)), None)
            cand = text_at.get(t)
            prior = [b for b in (story.bar_texts if story else []) if b != cand]
            shared = sorted(set().union(*(judge._terms(cand or "") & judge._terms(b) for b in prior))) if prior else []
            opened = r.get("rules_new")
            if opened:
                why, rule = "採用：開新的一則", "NEW-1"
            elif shared:
                why, rule = f"拒絕：與本則已收的標題共用「{'、'.join(shared[:6])}」→ 視為同一則主副標", "SAME-3"
            else:
                why, rule = "拒絕：有語音但沒有任何換題佐證（語音主題、主播鏡頭、名條、語音跟新標題）", "SAME-6"
            f = r.get("features") or {}
            heard = " ".join(dict.fromkeys(asr_at[s] for s in range(t, t + 30) if s in asr_at))[:120]
            out.append({"channel": ch, "sec": t, "minute": f"12{max(0, int(t // 60)):02d}" if t < 3600 else "1300",
                        "candidate_title": cand, "story_title": story.bar_texts[0] if story and story.bar_texts else None,
                        "story_start": story.start if story else None, "decision": "accept" if opened else "reject",
                        "reason": why, "rule": rule, "shared_terms": shared,
                        "asr_support": {k: f.get(k) for k in ("new2a", "new2b", "new2d", "asr_present")},
                        "asr_after": heard, "sim_to_story": round(f.get("sim", 0.0), 3)})
        for s in segs:
            if s.type.value == "news" and s.end is not None and s.end - s.start >= 300:
                gaps.append({"channel": ch, "start": s.start, "end": s.end, "minutes": round((s.end - s.start) / 60, 1),
                             "title": s.bar_texts[0] if s.bar_texts else None, "titles_absorbed": len(s.bar_texts)})
    p = Path(a.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as fh:
        for r in out:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    p.with_name(p.name.replace(".candidates.jsonl", ".long_stories.json")).write_text(
        json.dumps(gaps, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"→ {p}（{len(out)} 個候選點，拒絕 {sum(r['decision'] == 'reject' for r in out)}）；長段落 {len(gaps)} 則")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
