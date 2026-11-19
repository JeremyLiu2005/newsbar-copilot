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
    from .calibrate_ui import render as render_ui
    print(f"圖形化校正頁：{render_ui(frame, a.channel, ch, work / 'roi.html')}")
    for name, d in dirs.items():
        imgs = sorted(d.glob("*.jpg"))
        if not imgs:
            continue
        text, conf = join_lines(backend.read(imgs[0]))
        print(f"{name:15s} {rois[name]}  → {text!r} (conf {conf:.2f})  {imgs[0]}")
    if a.framed_at is not None:
        from .visual import save_framed_template
        roi = [float(x) for x in a.framed_roi.split(",")]
        out = work.parent / "framed.npy"
        save_framed_template(Path(a.video), a.framed_at, roi, out)
        print(f"包框樣板 → {out}\n請在 channels.yaml 該台加上：\n  framed_roi: {roi}\n  framed_ref: {out}")
    return 0


def _parse_video(spec: str, default_clock: str | None) -> tuple[str, Path, str | None]:
    """'tvbs=data/tvbs.mp4@11:59:50' → (tvbs, path, clock)."""
    ch, rest = spec.split("=", 1)
    path, _, clock = rest.partition("@")
    return ch, Path(path), clock or default_clock


def _resolve_clock(path: Path, cfg: dict, clock: str | None, backend) -> str | None:
    """--clock wins; otherwise read the on-screen clock (FR-24)."""
    if clock or not cfg.get("clock"):
        return clock
    from .clock import start_clock

    found = start_clock(path, cfg["clock"], backend)
    print(f"[{path.name}] 畫面時鐘校時 → 第 0 秒 = {found or '讀不到，視為 sheet 起點'}")
    return found


def _extract_job(args: tuple) -> tuple:
    """Runs in a worker process (spawn) so each channel has its own OCR engine."""
    from . import extract, fingerprint, ingest
    from .ocr import get_backend

    ch, path, clock, cfg, work, backend_name, asr_name, asr_model, lib_path, sheet_start, duration = args
    asr = None
    if asr_name != "none":
        from .asr import get_backend as get_asr
        asr = get_asr(asr_name, asr_model)
    backend = get_backend(backend_name)
    clock = _resolve_clock(Path(path), cfg, clock, backend)
    offset = ingest.sheet_offset(clock, sheet_start) if clock else 0
    obs, warns = extract.extract_channel(Path(path), ch, cfg, backend, Path(work), offset, asr,
                                         fingerprint.AdLibrary.load(Path(lib_path)), duration=duration)
    return ch, len(obs), warns


def cmd_run(a: argparse.Namespace) -> int:
    """Videos → obs → events → 12監看 xlsx + evidence + report + review page."""
    import time

    from . import evaluate, extract, ingest, llm, pipeline, review

    cfg = _load_yaml(a.channels)["channels"]
    rules = _load_yaml(a.rules)
    a.asr_model = a.asr_model or (rules.get("asr") or {}).get("model")
    work = Path(a.workdir)
    streams, warnings, timings = {}, {}, {}
    backend = asr = None
    from . import fingerprint
    lib_path = Path(((rules.get("fingerprint") or {}).get("library")) or work / "ad_library.npz")
    library = fingerprint.AdLibrary.load(lib_path)
    specs = [_parse_video(spec, a.clock) for spec in a.video]
    for ch, _, _ in specs:
        if ch not in cfg:
            raise SystemExit(f"設定檔沒有台別 {ch}")
    todo = [(ch, p, c) for ch, p, c in specs if not (a.resume and (work / ch / "obs.jsonl").exists())]
    if a.jobs > 1 and len(todo) > 1:  # four channels in parallel (NFR-05)
        import multiprocessing as mp
        from concurrent.futures import ProcessPoolExecutor, as_completed

        t0 = time.time()
        jobs = [(ch, str(p), c, cfg[ch], str(work), a.backend, a.asr, a.asr_model, str(lib_path), a.sheet_start, a.duration)
                for ch, p, c in todo]
        with ProcessPoolExecutor(max_workers=a.jobs, mp_context=mp.get_context("spawn")) as ex:
            futs = {ex.submit(_extract_job, j): j[0] for j in jobs}
            for f in as_completed(futs):
                ch = futs[f]
                try:
                    _, n, warns = f.result()
                    warnings[ch] = warns
                except Exception as e:  # NFR-09
                    warnings.setdefault(ch, []).append(f"處理失敗：{e}")
                    print(f"✗ [{ch}] {e}")
        timings["parallel_extract"] = round(time.time() - t0, 1)
        a.resume = True  # everything extracted above now loads from obs.jsonl
    for ch, path, clock in specs:
        obs_file = work / ch / "obs.jsonl"
        t0 = time.time()
        try:
            if a.resume and obs_file.exists():  # NFR-10
                print(f"[{ch}] 沿用 {obs_file}")
                streams[ch] = extract.load_obs(obs_file)
            else:
                if backend is None:
                    from .ocr import get_backend
                    backend = get_backend(a.backend)
                if a.asr != "none" and asr is None:
                    from .asr import get_backend as get_asr
                    asr = get_asr(a.asr, a.asr_model)
                clock = _resolve_clock(path, cfg[ch], clock, backend)
                offset = ingest.sheet_offset(clock, a.sheet_start) if clock else 0
                obs, warns = extract.extract_channel(path, ch, cfg[ch], backend, work, offset, asr,
                                                     library, duration=a.duration)
                streams[ch] = obs
                warnings[ch] = warns
        except Exception as e:  # NFR-09: one channel failing must not stop the others
            warnings.setdefault(ch, []).append(f"處理失敗：{e}")
            print(f"✗ [{ch}] {e}")
        timings[ch] = round(time.time() - t0, 1)
    headers = {k: v["excel_column_header"] for k, v in cfg.items() if k in streams}
    if not headers:
        raise SystemExit("沒有任何一台處理成功")
    out = Path(a.out)
    gen = llm.from_rules(rules)
    if gen is not None:
        gen = llm.CachedGenerator(gen, work / "label_cache.json")
    res = pipeline.run(streams, Path(a.template), out, headers, gen, rules, a.sheet_start, a.minutes)
    learned = _learn_ads(streams, work, library)
    if learned:
        library.save(lib_path)
    rep = evaluate.processing_report(res["evidence"], warnings, timings)
    rep["ad_library"] = {"path": str(lib_path), "seconds": int(len(library.blocks)), "learned": learned}
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
    if a.upload:
        import os

        from . import gdrive
        tok = gdrive.token_from(os.environ.get("NEWSBAR_GDRIVE_TOKEN"), a.token_cmd)
        print(f"→ Google 試算表：{gdrive.push(out, tok, a.drive_folder).get('webViewLink')}")
    return 0


def _learn_ads(streams: dict, work: Path, library) -> int:
    """FR-22: grow the ad library from breaks confirmed by ≥2 non-fingerprint signals."""
    import numpy as np

    from . import ad_state, fingerprint

    n = 0
    for ch, obs in streams.items():
        fp = work / ch / "audio_fp.npy"
        if not fp.exists() or not obs:
            continue
        bits = np.load(fp)
        first = min(o.sec for o in obs)
        for b in ad_state.detect(obs).breaks:
            if len(set(b.signals) - {"AD-f"}) < 2:
                continue
            secs = [s - first for s in range(b.start + 2, b.end - 2)]  # skip the edges
            n += fingerprint.learn(library, bits, secs)
    return n


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


def cmd_watch(a: argparse.Namespace) -> int:
    """NFR-24: process each hourly slot from the recorder's drop folder."""
    import time

    from . import ops

    cfg = _load_yaml(a.channels)["channels"]
    inbox, out_dir = Path(a.inbox), Path(a.out_dir)
    while True:
        for key, date, hhmm, files in ops.ready_slots(inbox, list(cfg), out_dir, a.grace_sec):
            missing = sorted(set(cfg) - set(files))
            print(f"[{key}] 處理 {', '.join(files)}" + (f"；缺 {', '.join(missing)}" if missing else ""))
            args = ["run", "--template", a.template, "--out", str(out_dir / f"{hhmm[:2]}監看_{date}.xlsx"),
                    "--channels", a.channels, "--rules", a.rules, "--workdir", str(Path(a.workdir) / key),
                    "--sheet-start", hhmm, "--jobs", str(a.jobs)]
            for ch, p in files.items():
                args += ["--video", f"{ch}={p}@{hhmm[:2]}:{hhmm[2:]}:00"]
            try:
                main(args)
                ops.mark_done(out_dir, key)
            except SystemExit as e:
                print(f"✗ [{key}] {e}")
        if a.keep_frames_days is not None:
            ops.cleanup_frames(Path(a.workdir), a.keep_frames_days)
        if a.once:
            return 0
        time.sleep(a.interval)


def cmd_search(a: argparse.Namespace) -> int:
    from .history import search

    rows = search(Path(a.root), a.q, a.channel, a.date_from, a.date_to)
    for r in rows:
        print(f"{r['date']} {r['minute']} {r['channel']:5s} {r['value']:12s} {r['bar_text'] or ''}")
    print(f"共 {len(rows)} 筆")
    return 0


def cmd_daily(a: argparse.Namespace) -> int:
    from . import history

    reps = [p for p in Path(a.root).rglob(f"*{a.date}*.report.json")]
    out = history.daily_report(reps, Path(a.out), a.date)
    print(f"日報 → {out}（{len(reps)} 個時段）")
    smtp = (_load_yaml(a.rules).get("daily_report") or {}).get("smtp")
    if a.send:
        if not smtp:
            raise SystemExit("rules.yaml 沒有 daily_report.smtp 設定，不寄送")
        history.send_report(out, smtp["to"], smtp)
        print("已寄出")
    return 0


def cmd_rules_check(a: argparse.Namespace) -> int:
    from . import history

    if a.record:
        rep = json.loads(Path(a.record).read_text(encoding="utf-8"))
        history.record_eval(Path(a.history), a.version, rep["change"]["f1"], rep["ad"]["f1"], a.date)
    if history.should_rollback(Path(a.history), a.version, a.threshold, a.weeks):
        prev = history.rollback(Path(a.rules_dir), Path(a.active), a.version, Path(a.history).with_name("rollback.log"))
        print(f"規則退版 {a.version} → {prev}" if prev else "需要退版，但沒有更早的版本")
    else:
        print("規則維持現版")
    return 0


def cmd_annotate(a: argparse.Namespace) -> int:
    from . import annotate, extract

    cfg = _load_yaml(a.channels)["channels"]
    work = Path(a.workdir)
    streams = {ch: extract.load_obs(work / ch / "obs.jsonl") for ch in cfg if (work / ch / "obs.jsonl").exists()}
    print(annotate.render(streams, {k: cfg[k]["name"] for k in streams}, Path(a.out), a.sheet_start))
    return 0


def cmd_gold_import(a: argparse.Namespace) -> int:
    from . import annotate

    cfg = _load_yaml(a.channels)["channels"]
    headers = {k: v["excel_column_header"] for k, v in cfg.items()}
    data = annotate.load(Path(a.annotation))
    cells = annotate.gold_cells(data["items"], headers.keys(), a.sheet_start, a.minutes)
    excel_writer.write(Path(a.template), Path(a.out), cells, headers)
    print(f"標準答案（{data['annotator']}）→ {a.out}")
    if a.other:
        from .minute_mapper import minute_labels
        other = annotate.load(Path(a.other))
        oc = annotate.gold_cells(other["items"], headers.keys(), a.sheet_start, a.minutes)
        starts = lambda cs: {ch: {m for m, c in v.items() if any(e.type.value == "news" for e in c.events)} for ch, v in cs.items()}
        k = annotate.kappa(starts(cells), starts(oc), minute_labels(a.sheet_start, a.minutes))
        print(f"兩位標註者換題分鐘一致性 Cohen's κ = {k:.2f}（目標 ≥0.8）")
    return 0


def cmd_upload(a: argparse.Namespace) -> int:
    import os

    from . import gdrive

    tok = gdrive.token_from(os.environ.get("NEWSBAR_GDRIVE_TOKEN"), a.token_cmd)
    res = gdrive.push(Path(a.sheet), tok, a.folder)
    print(f"已上傳到 Google 試算表：{res.get('webViewLink', res['id'])}")
    return 0


def cmd_cleanup(a: argparse.Namespace) -> int:
    from . import ops

    n = ops.cleanup_frames(Path(a.workdir), a.older_than_days)
    print(f"刪除 {n} 個截圖資料夾（文字結果保留）")
    return 0


def cmd_serve(a: argparse.Namespace) -> int:
    """Operator web UI on this machine: pick recordings, run, watch, download."""
    from . import server

    rules_fast = Path(a.rules_fast) if a.rules_fast else None
    s = server.Settings(video_dir=Path(a.video_dir), template=Path(a.template), out_dir=Path(a.out_dir),
                        channels=Path(a.channels), rules=Path(a.rules), rules_fast=rules_fast,
                        workdir=Path(a.workdir), asr=a.asr, jobs=a.jobs)
    httpd = server.serve(s, a.host, a.port)
    print(f"NewsBar Copilot 監看台：http://{a.host}:{a.port}（Ctrl+C 結束）", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
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
            s.add_argument("--framed-at", type=float, help="影片中包框廣告的某一秒，用來存包框樣板")
            s.add_argument("--framed-roi", default="0.65,0,0.35,0.78", help="包框時固定不變的區域 x,y,w,h")
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
    r.add_argument("--asr-model", help="例：small（Mac）、large-v3-turbo（GPU）")
    r.add_argument("--resume", action="store_true", help="已有 obs.jsonl 的台別直接沿用")
    r.add_argument("--jobs", type=int, default=1, help="平行處理台數（建議 4）")
    r.add_argument("--upload", action="store_true", help="完成後上傳到自己的 Google 雲端（試算表）")
    r.add_argument("--drive-folder")
    r.add_argument("--token-cmd")
    r.add_argument("--duration", type=float, help="只處理前 N 秒（測試用）")
    r.add_argument("--sheet-start", default="1200")
    r.add_argument("--minutes", type=int, default=60)
    r.set_defaults(func=cmd_run)

    sv = sub.add_parser("serve", help="在本機開監看員網頁：選影片、執行、看進度、下載 Excel")
    sv.add_argument("--video-dir", default="data/raw", help="側錄影片所在資料夾（只列這裡的檔案）")
    sv.add_argument("--template", default="data/raw/template.xlsx")
    sv.add_argument("--out-dir", default="out/web")
    sv.add_argument("--channels", default="config/channels.yaml")
    sv.add_argument("--rules", default="config/rules.yaml")
    sv.add_argument("--rules-fast", help="快速模式用的 rules（例：較小的語音模型）")
    sv.add_argument("--workdir", default="data/work")
    sv.add_argument("--asr", default="faster-whisper", choices=["none", "auto", "mlx", "faster-whisper"])
    sv.add_argument("--jobs", type=int, default=4)
    sv.add_argument("--host", default="127.0.0.1", help="預設只在本機開放；影片與表格不出這台電腦")
    sv.add_argument("--port", type=int, default=8765)
    sv.set_defaults(func=cmd_serve)

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
    wt = sub.add_parser("watch", help="監看側錄資料夾，每小時自動產表")
    wt.add_argument("--inbox", required=True, help="檔名格式 {channel}_{YYYYMMDD}_{HHMM}.mp4")
    wt.add_argument("--out-dir", required=True)
    wt.add_argument("--template", required=True)
    wt.add_argument("--channels", default="config/channels.example.yaml")
    wt.add_argument("--rules", default="config/rules.example.yaml")
    wt.add_argument("--workdir", default="data/work")
    wt.add_argument("--jobs", type=int, default=4)
    wt.add_argument("--grace-sec", type=float, default=900, help="等其他台檔案的最長秒數")
    wt.add_argument("--interval", type=float, default=60)
    wt.add_argument("--keep-frames-days", type=float)
    wt.add_argument("--once", action="store_true")
    wt.set_defaults(func=cmd_watch)

    se = sub.add_parser("search", help="歷史檢索（NFR-27）")
    se.add_argument("--root", default="out")
    se.add_argument("--q")
    se.add_argument("--channel")
    se.add_argument("--date-from")
    se.add_argument("--date-to")
    se.set_defaults(func=cmd_search)

    dy = sub.add_parser("daily", help="日報 HTML，可選擇寄送（FR-18）")
    dy.add_argument("--root", default="out")
    dy.add_argument("--date", required=True, help="YYYYMMDD")
    dy.add_argument("--out", required=True)
    dy.add_argument("--rules", default="config/rules.example.yaml")
    dy.add_argument("--send", action="store_true")
    dy.set_defaults(func=cmd_daily)

    rc = sub.add_parser("rules-check", help="記錄評估結果，連兩週未達標自動退版（FR-21）")
    rc.add_argument("--history", default="out/eval_history.jsonl")
    rc.add_argument("--record", help="newsbar eval 輸出的 json")
    rc.add_argument("--version", required=True)
    rc.add_argument("--date", required=True)
    rc.add_argument("--rules-dir", default="config/rules")
    rc.add_argument("--active", default="config/rules.yaml")
    rc.add_argument("--threshold", type=float, default=0.75)
    rc.add_argument("--weeks", type=int, default=2)
    rc.set_defaults(func=cmd_rules_check)

    an = sub.add_parser("annotate", help="產生標註頁（標準答案用）")
    an.add_argument("--workdir", required=True)
    an.add_argument("--out", required=True)
    an.add_argument("--channels", default="config/channels.example.yaml")
    an.add_argument("--sheet-start", default="1200")
    an.set_defaults(func=cmd_annotate)

    gi = sub.add_parser("gold-import", help="標註 JSON → 標準答案 xlsx，可算兩人 κ")
    gi.add_argument("--annotation", required=True)
    gi.add_argument("--other", help="第二位標註者的 JSON")
    gi.add_argument("--template", required=True)
    gi.add_argument("--out", required=True)
    gi.add_argument("--channels", default="config/channels.example.yaml")
    gi.add_argument("--sheet-start", default="1200")
    gi.add_argument("--minutes", type=int, default=60)
    gi.set_defaults(func=cmd_gold_import)

    up = sub.add_parser("upload", help="監看表上傳到自己的 Google 雲端（轉成試算表，之後更新同一份）")
    up.add_argument("--sheet", required=True)
    up.add_argument("--folder", help="雲端資料夾 ID")
    up.add_argument("--token-cmd", help="取得 OAuth 權杖的指令，例：gcloud auth print-access-token")
    up.set_defaults(func=cmd_upload)

    cl = sub.add_parser("cleanup", help="刪除過期截圖（NFR-17）")
    cl.add_argument("--workdir", default="data/work")
    cl.add_argument("--older-than-days", type=float, default=7)
    cl.set_defaults(func=cmd_cleanup)
    a = p.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    raise SystemExit(main())
