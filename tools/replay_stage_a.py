"""Stage A replay: reuse saved extraction (obs.jsonl) and rerun only the decision pipeline.

Nothing in the shared work folder is written: the label cache is copied into the output folder
first, and the ad library is never updated. Usage:

  uv run python tools/replay_stage_a.py --work /abs/data/work_rerun_1122_asr --out /abs/out/stageA/12監看_20261106.xlsx \
      [--rules config/rules.1106.yaml] [--seed-cache /abs/label_cache.json] [--no-llm]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from newsbar import evaluate, excel_writer, extract, lexicon, llm, pipeline, runinfo  # noqa: E402
from newsbar.cli import _load_clock_info, _load_yaml, _offset  # noqa: E402

CHANNELS = ["tvbs", "ettv", "ftv", "cts"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--template", default="/Users/rei/newsbar-copilot/data/raw/template.xlsx")
    ap.add_argument("--rules", default=str(ROOT / "config/rules.1106.yaml"))
    ap.add_argument("--channels", default=str(ROOT / "config/channels.1106.yaml"))
    ap.add_argument("--lexicon", default=str(ROOT / "config/lexicon.yaml"))
    ap.add_argument("--seed-cache", help="label cache to start from (copied, never written)")
    ap.add_argument("--no-llm", action="store_true")
    a = ap.parse_args()
    started = dt.datetime.now()
    run_id = runinfo.new_run_id(started)
    work, out = Path(a.work), Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    cfg = _load_yaml(a.channels)["channels"]
    rules = _load_yaml(a.rules)
    lexicon.use(a.lexicon)
    streams, clock_info = {}, {}
    for ch in CHANNELS:
        streams[ch] = extract.load_obs(work / ch / "obs.jsonl")
        if info := _load_clock_info(work, ch):
            clock_info[ch] = info
    headers = {ch: cfg[ch]["excel_column_header"] for ch in CHANNELS}
    gen = None if a.no_llm else llm.from_rules(rules)
    cache = out.parent / "label_cache.json"
    cache_seeded = False
    if gen is not None:
        seed = Path(a.seed_cache) if a.seed_cache else work / "label_cache.json"
        if seed.exists() and not cache.exists():
            shutil.copy(seed, cache)
            cache_seeded = True
        gen = llm.CachedGenerator(llm.GuardedGenerator(gen), cache)
    reasons = {ch: [i["reason"]] for ch, i in clock_info.items() if i.get("reason")}
    res = pipeline.run(streams, Path(a.template), out, headers, gen, rules, "1200", 60,
                       channel_reasons=reasons, run_id=run_id, channels=CHANNELS)
    rep = evaluate.processing_report(res["evidence"], {}, {})
    rep["run_id"] = run_id
    rep["delivery"] = res["delivery"]
    out.with_suffix(".report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    review_xlsx = out.with_name(out.stem + "_複查版.xlsx")
    n = excel_writer.write_review_copy(out, review_xlsx, res["evidence"], headers, res["failed"],
                                       {ch: _offset(i, "1200") for ch, i in clock_info.items()})
    man = runinfo.manifest(
        run_id, started=started.isoformat(timespec="seconds"), finished=dt.datetime.now().isoformat(timespec="seconds"),
        inputs={}, template=Path(a.template), channels_cfg=cfg, rules=rules, lexicon_path=a.lexicon,
        ocr_backend="reused", asr="reused", asr_model=None, llm_model=getattr(llm.from_rules(rules), "model", None),
        llm_state="off" if gen is None else "on",
        resumed={ch: {"mode": "reused", "stages": runinfo.load_stages(work, ch)} for ch in CHANNELS},
        out_files=[str(out), str(out.with_suffix(".evidence.jsonl")), str(out.with_suffix(".stories.jsonl")),
                   str(out.with_suffix(".report.json")), str(review_xlsx)])
    man["stage"] = "A (reused extraction, decision pipeline only)"
    man["extraction_dir"] = str(work)
    man["label_cache"] = {"used": gen is not None, "seeded_from_previous": cache_seeded,
                          "path": str(cache) if gen is not None else None}
    out.with_suffix(".manifest.json").write_text(json.dumps(man, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"→ {out}  ({run_id}); review copy marks {n} cells")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
