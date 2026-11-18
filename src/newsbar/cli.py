"""newsbar CLI.

  newsbar write-sheet --events events.jsonl --template 範本.xlsx --out out/12監看_20261106.xlsx
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from . import evidence, excel_writer
from .minute_mapper import map_events
from .schemas import Event, Labels


def _load_yaml(p: str | None) -> dict:
    return yaml.safe_load(Path(p).read_text(encoding="utf-8")) if p else {}


def cmd_write_sheet(a: argparse.Namespace) -> int:
    chan_cfg = _load_yaml(a.channels)["channels"]
    rules = _load_yaml(a.rules)
    headers = {k: v["excel_column_header"] for k, v in chan_cfg.items()}
    events = [Event.from_dict(json.loads(line)) for line in Path(a.events).read_text(encoding="utf-8").splitlines() if line.strip()]
    cells = map_events(events, headers.keys(), a.start, a.minutes, Labels.from_rules(rules))
    out = Path(a.out)
    excel_writer.write(Path(a.template), out, cells, headers)
    recs = evidence.build(cells, str(rules.get("version", "0")))
    evidence.save(recs, out.with_suffix(".evidence.jsonl"), out.with_suffix(".report.csv"))
    n = sum(len(v) for v in cells.values())
    print(f"寫入 {n} 格 → {out}")
    return 0


def _channel(a: argparse.Namespace) -> dict:
    cfg = _load_yaml(a.channels)["channels"]
    if a.channel not in cfg:
        raise SystemExit(f"設定檔沒有台別 {a.channel}，可用：{', '.join(cfg)}")
    return cfg[a.channel]


def cmd_ocr(a: argparse.Namespace) -> int:
    from . import ingest
    from .ocr import get_backend
    from .ocr.stream import VoteParams, ocr_channel, save_jsonl

    ch = _channel(a)
    rules = _load_yaml(a.rules)
    rois = {k: v for k, v in ch.items() if k in ingest.ROI_NAMES and k in ("main_bar", "ticker")}
    work = Path(a.workdir) / a.channel
    dirs = ingest.extract_rois(Path(a.video), rois, work / "frames", start=a.start_sec, duration=a.duration)
    offset = ingest.sheet_offset(a.clock, a.sheet_start) if a.clock else 0
    o = rules.get("ocr", {})
    res = ocr_channel(get_backend(a.backend), dirs, offset,
                      VoteParams(min_conf=o.get("min_conf"), window_sec=o.get("vote_window_sec", 5)))
    save_jsonl(res.raw, work / "ocr_raw.jsonl")
    save_jsonl(res.voted, work / "ocr.jsonl")
    changes = sum(1 for x, y in zip(res.voted, res.voted[1:]) if x.text != y.text)
    print(f"{a.channel}: {len(res.voted)} 秒，文字變化 {changes} 次 → {work / 'ocr.jsonl'}")
    for w in res.warnings:
        print("⚠", w)
    return 0


def cmd_calibrate(a: argparse.Namespace) -> int:
    """FR-17 (CLI form): dump a frame + ROI crops and print what OCR reads in each."""
    from . import ingest
    from .ocr import get_backend, join_lines

    ch = _channel(a)
    work = Path(a.workdir) / a.channel / "calibrate"
    frame = ingest.extract_frame(Path(a.video), a.at, work / "frame.png")
    rois = {k: v for k, v in ch.items() if k in ingest.ROI_NAMES}
    dirs = ingest.extract_rois(Path(a.video), rois, work, start=a.at, duration=1)
    backend = get_backend(a.backend)
    print(f"整張畫面：{frame}")
    for name, d in dirs.items():
        imgs = sorted(d.glob("*.jpg"))
        if not imgs:
            continue
        text, conf = join_lines(backend.read(imgs[0]))
        print(f"{name:15s} {rois[name]}  → {text!r} (conf {conf:.2f})  {imgs[0]}")
    return 0


def _parse_video(spec: str, default_clock: str | None) -> tuple[str, Path, str | None]:
    """'tvbs=data/tvbs.mp4@11:59:50' → (tvbs, path, clock)."""
    ch, rest = spec.split("=", 1)
    path, _, clock = rest.partition("@")
    return ch, Path(path), clock or default_clock


def cmd_run(a: argparse.Namespace) -> int:
    """Videos → obs → events → 12監看 xlsx + evidence + report + review page."""
    import time

    from . import evaluate, extract, ingest, llm, pipeline, review

    cfg = _load_yaml(a.channels)["channels"]
    rules = _load_yaml(a.rules)
    work = Path(a.workdir)
    streams, warnings, timings = {}, {}, {}
    backend = asr = None
    for spec in a.video:
        ch, path, clock = _parse_video(spec, a.clock)
        if ch not in cfg:
            raise SystemExit(f"設定檔沒有台別 {ch}")
        obs_file = work / ch / "obs.jsonl"
        t0 = time.time()
        try:
            if a.resume and obs_file.exists():  # NFR-13
                print(f"[{ch}] 沿用 {obs_file}")
                streams[ch] = extract.load_obs(obs_file)
            else:
                if backend is None:
                    from .ocr import get_backend
                    backend = get_backend(a.backend)
                if a.asr != "none" and asr is None:
                    from .asr import get_backend as get_asr
                    asr = get_asr(a.asr)
                offset = ingest.sheet_offset(clock, a.sheet_start) if clock else 0
                obs, warns = extract.extract_channel(path, ch, cfg[ch], backend, work, offset, asr,
                                                     duration=a.duration)
                streams[ch] = obs
                warnings[ch] = warns
        except Exception as e:  # NFR-12: one channel failing must not stop the others
            warnings.setdefault(ch, []).append(f"處理失敗：{e}")
            print(f"✗ [{ch}] {e}")
        timings[ch] = round(time.time() - t0, 1)
    headers = {k: v["excel_column_header"] for k, v in cfg.items() if k in streams}
    if not headers:
        raise SystemExit("沒有任何一台處理成功")
    out = Path(a.out)
    res = pipeline.run(streams, Path(a.template), out, headers, llm.from_rules(rules), rules, a.sheet_start, a.minutes)
    rep = evaluate.processing_report(res["evidence"], warnings, timings)
    out.with_suffix(".report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    review.render(res["evidence"], {k: cfg[k]["name"] for k in headers}, out.with_suffix(".review.html"),
                  a.sheet_start, a.minutes)
    for ch, d in rep["channels"].items():
        flag = "⚠ fallback 過高" if d["alert"] else ""
        print(f"{ch}: {d['cells']} 格，換題 {d['changes']}，廣告 {d['ads']}，低信心 {d['low_conf']} {flag}")
    for ch, ws in warnings.items():
        for w in ws:
            print(f"⚠ [{ch}] {w}")
    print(f"→ {out}\n→ {out.with_suffix('.review.html')}")
    return 0


def cmd_eval(a: argparse.Namespace) -> int:
    from . import evaluate

    cfg = _load_yaml(a.channels)["channels"]
    headers = {k: v["excel_column_header"] for k, v in cfg.items()}
    sysd = evaluate.read_sheet(Path(a.system), headers)
    gold = evaluate.read_sheet(Path(a.gold), headers)
    rng = (a.test_from, a.test_to) if a.test_from else None
    rep = evaluate.evaluate(sysd, gold, a.sheet_start, a.minutes, a.tolerance, rng)
    Path(a.out).write_text(rep.to_json(), encoding="utf-8")
    print(f"換題 F1 {rep.change['f1']}（P {rep.change['precision']} R {rep.change['recall']}）"
          f"｜廣告 F1 {rep.ad['f1']}｜錯誤 {len(rep.errors)} 筆 → {a.out}")
    return 0


def cmd_review(a: argparse.Namespace) -> int:
    from . import review

    cfg = _load_yaml(a.channels)["channels"]
    recs = [json.loads(x) for x in Path(a.evidence).read_text(encoding="utf-8").splitlines() if x.strip()]
    names = {k: v["name"] for k, v in cfg.items() if any(r["channel"] == k for r in recs)}
    print(review.render(recs, names, Path(a.out)))
    return 0


def cmd_apply_corrections(a: argparse.Namespace) -> int:
    """FR-16: original stays; write a separate corrected copy from the template."""
    from . import evaluate, review
    from .schemas import Cell

    cfg = _load_yaml(a.channels)["channels"]
    headers = {k: v["excel_column_header"] for k, v in cfg.items()}
    cells = evaluate.read_sheet(Path(a.sheet), headers)
    fixed = review.apply_corrections(cells, review.load_corrections(Path(a.corrections)))
    as_cells = {ch: {m: Cell(ch, m, v, []) for m, v in col.items() if v} for ch, col in fixed.items()}
    excel_writer.write(Path(a.template), Path(a.out), as_cells, {k: headers[k] for k in as_cells})
    print(f"修正版 → {a.out}（原檔未改動）")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="newsbar")
    sub = p.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("write-sheet", help="事件 JSONL → 12監看 xlsx")
    w.add_argument("--events", required=True)
    w.add_argument("--template", required=True)
    w.add_argument("--out", required=True)
    w.add_argument("--channels", default="config/channels.example.yaml")
    w.add_argument("--rules", default="config/rules.example.yaml")
    w.add_argument("--start", default="1200")
    w.add_argument("--minutes", type=int, default=60)
    w.set_defaults(func=cmd_write_sheet)

    for name, func, help_ in (("ocr", cmd_ocr, "影片 → 主 bar 文字流"), ("calibrate", cmd_calibrate, "檢查 ROI 框得對不對")):
        s = sub.add_parser(name, help=help_)
        s.add_argument("--video", required=True)
        s.add_argument("--channel", required=True)
        s.add_argument("--channels", default="config/channels.example.yaml")
        s.add_argument("--rules", default="config/rules.example.yaml")
        s.add_argument("--workdir", default="data/work")
        s.add_argument("--backend", default="auto", choices=["auto", "vision", "paddle"])
        if name == "ocr":
            s.add_argument("--clock", help="影片第 0 秒的實際時刻，例：11:59:50")
            s.add_argument("--sheet-start", default="1200")
            s.add_argument("--start-sec", type=float, default=0.0)
            s.add_argument("--duration", type=float)
        else:
            s.add_argument("--at", type=float, default=30.0, help="取第幾秒的畫面")
        s.set_defaults(func=func)

    r = sub.add_parser("run", help="四台影片 → 監看表、依據、報告、抽查頁")
    r.add_argument("--video", action="append", required=True, help="tvbs=data/tvbs.mp4[@11:59:50]，可重複")
    r.add_argument("--clock", help="所有影片第 0 秒的實際時刻（個別指定用 @）")
    r.add_argument("--template", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--channels", default="config/channels.example.yaml")
    r.add_argument("--rules", default="config/rules.example.yaml")
    r.add_argument("--workdir", default="data/work")
    r.add_argument("--backend", default="auto", choices=["auto", "vision", "paddle"])
    r.add_argument("--asr", default="none", choices=["none", "auto", "mlx", "faster-whisper"])
    r.add_argument("--resume", action="store_true", help="已有 obs.jsonl 的台別直接沿用")
    r.add_argument("--duration", type=float, help="只處理前 N 秒（測試用）")
    r.add_argument("--sheet-start", default="1200")
    r.add_argument("--minutes", type=int, default=60)
    r.set_defaults(func=cmd_run)

    ev = sub.add_parser("eval", help="系統結果 vs 標準答案")
    ev.add_argument("--system", required=True)
    ev.add_argument("--gold", required=True)
    ev.add_argument("--out", default="out/eval.json")
    ev.add_argument("--channels", default="config/channels.example.yaml")
    ev.add_argument("--tolerance", type=int, default=1)
    ev.add_argument("--test-from", help="只評估此分鐘之後，例 1220（前段留給調參）")
    ev.add_argument("--test-to", default="2359")
    ev.add_argument("--sheet-start", default="1200")
    ev.add_argument("--minutes", type=int, default=60)
    ev.set_defaults(func=cmd_eval)

    rv = sub.add_parser("review", help="依據 → 抽查頁 HTML")
    rv.add_argument("--evidence", required=True)
    rv.add_argument("--out", required=True)
    rv.add_argument("--channels", default="config/channels.example.yaml")
    rv.set_defaults(func=cmd_review)

    ac = sub.add_parser("apply-corrections", help="抽查修正 → 另存修正版")
    ac.add_argument("--sheet", required=True)
    ac.add_argument("--corrections", required=True)
    ac.add_argument("--template", required=True)
    ac.add_argument("--out", required=True)
    ac.add_argument("--channels", default="config/channels.example.yaml")
    ac.set_defaults(func=cmd_apply_corrections)
    a = p.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    raise SystemExit(main())
