"""newsbar CLI.

  newsbar write-sheet --events events.jsonl --template 範本.xlsx --out out/12監看_20261106.xlsx
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from . import evidence, excel_writer, ingest
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


CLOCK_FROM_FILENAME = "時鐘未讀到，改用檔名時間"
CLOCK_UNCALIBRATED = "時間未校正"


def resolve_clock_info(path: Path, cfg: dict, clock: str | None, backend, log=print) -> dict:
    """Where video second 0 sits on the wall clock, and how we know (R11/R19).

    --clock wins; otherwise the on-screen clock (FR-24); if that cannot be read, the time in the
    recording's file name; if even that is missing the sheet start is assumed — and never silently:
    the result carries a `reason` that flags every cell of the channel for review."""
    if clock:
        return {"source": "cli", "value": clock, "reason": None}
    found = None
    if cfg.get("clock"):
        from .clock import start_clock

        try:
            found = start_clock(path, cfg["clock"], backend)
        except Exception as e:  # noqa: BLE001 - an unreadable clock must not stop the channel
            log(f"[{path.name}] 讀畫面時鐘時出錯：{e}")
    if found:
        log(f"[{path.name}] 畫面時鐘校時 → 第 0 秒 = {found}")
        return {"source": "screen", "value": found, "reason": None}
    named = ingest.clock_from_filename(path)
    if named:
        log(f"⚠ [{path.name}] 畫面時鐘讀不到，改用檔名時間 {named} 當第 0 秒；該台每格標為待複查")
        return {"source": "filename", "value": named, "reason": CLOCK_FROM_FILENAME}
    log(f"⚠ [{path.name}] 畫面時鐘讀不到，檔名也沒有時間；假設第 0 秒 = 時段起點，該台每格標為待複查")
    return {"source": "none", "value": None, "reason": CLOCK_UNCALIBRATED}


def _offset(info: dict, sheet_start: str) -> int:
    return ingest.sheet_offset(info["value"], sheet_start) if info.get("value") else 0


def _clock_file(work: Path, ch: str) -> Path:
    return work / ch / "clock.json"


def _save_clock_info(work: Path, ch: str, info: dict) -> None:
    _clock_file(work, ch).parent.mkdir(parents=True, exist_ok=True)
    _clock_file(work, ch).write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")


def _load_clock_info(work: Path, ch: str) -> dict | None:
    f = _clock_file(work, ch)
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def _extract_job(args: tuple) -> tuple:
    """Runs in a worker process (spawn) so each channel has its own OCR engine."""
    from . import extract, fingerprint, ingest
    from .ocr import get_backend

    ch, path, clock, cfg, work, backend_name, asr_name, asr_model, lib_path, sheet_start, duration, asr_parallel = args
    asr = None
    if asr_name != "none":
        from .asr import get_backend as get_asr
        asr = get_asr(asr_name, asr_model)
    backend = get_backend(backend_name)
    info = resolve_clock_info(Path(path), cfg, clock, backend)
    _save_clock_info(Path(work), ch, info)
    offset = _offset(info, sheet_start)
    obs, warns = extract.extract_channel(Path(path), ch, cfg, backend, Path(work), offset, asr,
                                         fingerprint.AdLibrary.load(Path(lib_path)), duration=duration,
                                         asr_parallel=asr_parallel)
    return ch, len(obs), warns + ([f"{info['reason']}（{info['value'] or '無'}）"] if info.get("reason") else []), info


def _resolve_inputs(a: argparse.Namespace, cfg: dict) -> tuple[list[tuple[str, Path, str | None]], Path, Path, dict[str, str]]:
    """--video specs (explicit wins) plus, with --input-dir, the videos and template found in that
    folder. Returns (specs, template, out, channels with no input)."""
    from . import discover

    specs = [_parse_video(spec, a.clock) for spec in (a.video or [])]
    missing: dict[str, str] = {}
    template = Path(a.template) if a.template else None
    if a.input_dir:
        folder = Path(a.input_dir)
        if not folder.is_dir():
            raise SystemExit(f"--input-dir 不是資料夾：{folder}")
        found, warns = discover.find_videos(folder, cfg)
        have = {c for c, _, _ in specs}
        specs += [(ch, p, a.clock) for ch, p in found.items() if ch not in have]
        for w in warns:
            print(f"⚠ {w}")
        for ch in cfg:
            if ch not in {c for c, _, _ in specs}:
                missing[ch] = f"{folder} 裡找不到 {cfg[ch].get('name', ch)} 的影片"
                print(f"⚠ {missing[ch]}；這一台會是空的，列入複查")
        if template is None:
            template, twarns = discover.find_template(folder)
            for w in twarns:
                print(f"⚠ {w}")
            if template is None:
                raise SystemExit(f"{folder} 裡沒有 .xlsx 範本，請用 --template 指定")
            print(f"範本：{template}")
    if template is None:
        raise SystemExit("請用 --template 指定範本，或用 --input-dir 讓程式在資料夾裡找")
    if not specs:
        raise SystemExit("沒有影片：請用 --video 或 --input-dir")
    out = Path(a.out) if a.out else Path("out") / "12監看.xlsx"
    return specs, template, out, missing


def _model_facts(backend: str, rules: dict) -> dict:
    """RUN-2: what actually ran and what left the machine, for the manifest (no keys, no secrets)."""
    import os

    from . import llm as _llm
    from . import vision as _vision
    try:
        from .ocr.base import get_backend
        ocr = type(get_backend(backend)).__name__
        ocr = {"VisionBackend": "Apple Vision", "PaddleBackend": "PaddleOCR"}.get(ocr, ocr)
    except Exception as e:  # noqa: BLE001
        ocr = f"無法判定（{type(e).__name__}）"
    lab = (rules or {}).get("labeler") or {}
    kind = lab.get("generator", "rule")
    facts: dict = {"ocr_backend_resolved": ocr, "llm_generator": kind}
    if kind in ("nvidia", "openai_compat"):
        facts["llm_endpoint"] = os.environ.get("NEWSBAR_LLM_BASE_URL") or lab.get("base_url") or _llm.NVIDIA_BASE_URL
        facts["llm_sends"] = "主標題文字（讀不到標題時附 200 字內語音）"
        if lab.get("fallback") == "ollama":
            facts["llm_fallback_model"] = lab.get("fallback_model", _llm.OllamaGenerator.model)
            facts["llm_fallback_host"] = lab.get("host", _llm.OllamaGenerator.host)
    if lab.get("image_rewrite"):
        facts["image_rewrite_model"] = (os.environ.get("NEWSBAR_VISION_MODEL") or lab.get("image_model")
                                        or "nvidia/nemotron-3.5-super-vl-120b-a12b")
        facts["image_rewrite_endpoint"] = os.environ.get("NEWSBAR_VISION_BASE_URL", _vision.NVIDIA_URL)
        facts["image_rewrite_sends"] = "主標題列裁切圖（單張 JPEG）＋標題文字＋五字標規則"
    vmode = os.environ.get("NEWSBAR_VISION_MODE") or ((rules or {}).get("vision") or {}).get("mode") or "off"
    facts["vision_reread"] = vmode
    if vmode not in ("off", "mock"):
        facts["vision_reread_model"] = os.environ.get("NEWSBAR_VISION_MODEL", "")
    return facts


def cmd_run(a: argparse.Namespace) -> int:
    """Videos → obs → events → 12監看 xlsx + evidence + report + review page."""
    import time

    import datetime as _dt

    from . import evaluate, extract, ingest, lexicon, llm, pipeline, review, runinfo

    started = _dt.datetime.now()
    run_id = runinfo.new_run_id(started)
    cfg = _load_yaml(a.channels)["channels"]
    rules = _load_yaml(a.rules)
    lex_path = a.lexicon or rules.get("lexicon") or ("config/lexicon.yaml" if Path("config/lexicon.yaml").exists() else None)
    lexicon.use(lex_path)  # place names, hedge markers, overlay tokens (config/lexicon.yaml)
    asr_cfg = rules.get("asr") or {}
    a.asr_model = a.asr_model or asr_cfg.get("model")
    # Mac: one transcription at a time; NVIDIA GPU: several (asr.parallel / --asr-parallel)
    if a.asr_parallel is None:
        a.asr_parallel = int(asr_cfg.get("parallel") or extract.default_asr_parallel())
    import os
    for key, env in (("device", "NEWSBAR_ASR_DEVICE"), ("compute_type", "NEWSBAR_ASR_COMPUTE")):
        if asr_cfg.get(key):  # inherited by the spawned per-channel workers
            os.environ[env] = str(asr_cfg[key])
    work = Path(a.workdir)
    streams, warnings, timings = {}, {}, {}
    clock_info: dict[str, dict] = {}
    backend = asr = None
    from . import fingerprint
    lib_path = Path(((rules.get("fingerprint") or {}).get("library")) or work / "ad_library.npz")
    library = fingerprint.AdLibrary.load(lib_path)
    specs, template, out, failed = _resolve_inputs(a, cfg)
    for ch, _, _ in specs:
        if ch not in cfg:
            raise SystemExit(f"設定檔沒有台別 {ch}")
    # 規則 RUN-1：--resume 只沿用「影片、該台擷取設定、OCR/語音後端都沒變」的擷取結果
    metas = {ch: runinfo.extract_meta(p, cfg[ch], a.backend, a.asr, a.asr_model, a.duration) for ch, p, _ in specs}
    reuse: dict[str, bool] = {}
    extraction: dict[str, str] = {}
    for ch, _, _ in specs:
        ok, why = runinfo.resume_check(work, ch, metas[ch]) if a.resume else (False, None)
        reuse[ch] = ok
        extraction[ch] = "reused" if ok else "extracted"
        if why:
            warnings.setdefault(ch, []).append(why)
            print(f"⚠ [{ch}] {why}")
    todo = [(ch, p, c) for ch, p, c in specs if not reuse[ch]]
    if a.jobs > 1 and len(todo) > 1:  # four channels in parallel (NFR-05)
        import multiprocessing as mp
        from concurrent.futures import ProcessPoolExecutor, as_completed

        t0 = time.time()
        jobs = [(ch, str(p), c, cfg[ch], str(work), a.backend, a.asr, a.asr_model, str(lib_path), a.sheet_start, a.duration,
                 a.asr_parallel)
                for ch, p, c in todo]
        with ProcessPoolExecutor(max_workers=a.jobs, mp_context=mp.get_context("spawn")) as ex:
            futs = {ex.submit(_extract_job, j): j[0] for j in jobs}
            for f in as_completed(futs):
                ch = futs[f]
                try:
                    _, n, warns, info = f.result()
                    warnings[ch] = warns
                    clock_info[ch] = info
                except Exception as e:  # NFR-09
                    warnings.setdefault(ch, []).append(f"處理失敗：{e}")
                    failed[ch] = f"擷取失敗（{runinfo.classify_error(e)}）：{e}"
                    print(f"✗ [{ch}] {e}")
        timings["parallel_extract"] = round(time.time() - t0, 1)
        for ch, _, _ in todo:
            if ch not in failed:
                runinfo.save_meta(work, ch, metas[ch])
                reuse[ch] = True  # extracted above: now loads from obs.jsonl
    for ch, path, clock in specs:
        if ch in failed:  # already failed in the parallel pass; do not run the same hour again
            continue
        obs_file = work / ch / "obs.jsonl"
        t0 = time.time()
        try:
            if reuse.get(ch) and obs_file.exists():  # NFR-10, RUN-1
                print(f"[{ch}] 沿用 {obs_file}")
                streams[ch] = extract.load_obs(obs_file)
                if ch not in clock_info and (saved := _load_clock_info(work, ch)):
                    clock_info[ch] = saved
            else:
                if backend is None:
                    from .ocr import get_backend
                    backend = get_backend(a.backend)
                if a.asr != "none" and asr is None:
                    from .asr import get_backend as get_asr
                    asr = get_asr(a.asr, a.asr_model)
                info = resolve_clock_info(path, cfg[ch], clock, backend)
                _save_clock_info(work, ch, info)
                clock_info[ch] = info
                obs, warns = extract.extract_channel(path, ch, cfg[ch], backend, work, _offset(info, a.sheet_start), asr,
                                                     library, duration=a.duration, asr_parallel=a.asr_parallel)
                streams[ch] = obs
                runinfo.save_meta(work, ch, metas[ch])
                warnings[ch] = warnings.get(ch, []) + warns + (
                    [f"{info['reason']}（{info['value'] or '無'}）"] if info.get("reason") else [])
        except Exception as e:  # NFR-09: one channel failing must not stop the others
            warnings.setdefault(ch, []).append(f"處理失敗：{e}")
            failed[ch] = f"擷取失敗（{runinfo.classify_error(e)}）：{e}"
            print(f"✗ [{ch}] {e}")
        timings[ch] = round(time.time() - t0, 1)
    for ch in [c for c in streams if c in failed]:
        streams.pop(ch)
    headers = {k: v["excel_column_header"] for k, v in cfg.items() if k in streams}
    if not headers:
        raise SystemExit("沒有任何一台處理成功：" + "；".join(f"{c} {m}" for c, m in failed.items()))
    lab_cfg = rules.setdefault("labeler", {}) if isinstance(rules, dict) else {}
    if lab_cfg.get("image_rewrite") and not lab_cfg.get("image_cache"):
        lab_cfg["image_cache"] = str(work / "label_cache_image.json")  # IMG-1 answers cached: reruns repeat
    gen = llm.from_rules(rules, getattr(a, "kb", None))
    llm_model = getattr(gen, "model", None)
    llm_state = "off" if gen is None else "on"
    if gen is not None:
        if isinstance(gen, llm.ChainGenerator):
            # R21-2: each model keeps its own breaker and cache (the local answers stay in label_cache.json)
            local = gen.secondary
            if not (llm.ensure_ollama(local.host) and llm.warm_up(local)):
                local = llm.UnavailableGenerator()
            gen.primary = llm.CachedGenerator(llm.GuardedGenerator(gen.primary), work / "label_cache_cloud.json")
            gen.secondary = llm.CachedGenerator(llm.GuardedGenerator(local), work / "label_cache.json")
        else:
            host = getattr(gen, "host", None)
            # R20: no AI service is not a reason to stop; R20-2: load the model before four channels ask at once
            if host and not (llm.ensure_ollama(host) and llm.warm_up(gen)):
                gen = llm.UnavailableGenerator()
                llm_state = "unavailable"
            else:
                gen = llm.CachedGenerator(llm.GuardedGenerator(gen), work / "label_cache.json")
    reasons = {ch: [i["reason"]] for ch, i in clock_info.items() if i.get("reason") and ch in streams}
    try:
        res = pipeline.run(streams, template, out, headers, gen, rules, a.sheet_start, a.minutes,
                           channel_reasons=reasons, prior_failed=failed, run_id=run_id,
                           channels=[ch for ch, _, _ in specs])
    except pipeline.PipelineError as e:
        raise SystemExit(str(e))
    failed = res["failed"]
    learned = _learn_ads(streams, work, library)
    if learned:
        library.save(lib_path)
    rep = evaluate.processing_report(res["evidence"], warnings, timings)
    rep["ad_library"] = {"path": str(lib_path), "seconds": int(len(library.blocks)), "learned": learned}
    rep["failed_channels"] = failed  # R20: visible here, in the 複查清單 and the summary — never in the workbook
    rep["clock"] = clock_info
    rep["pipeline_warnings"] = res["warnings"]
    rep["run_id"] = run_id
    rep["execution_status"] = {ch: ("FAILED" if ch in failed else "SUCCEEDED") for ch, _, _ in specs}
    rep["delivery"] = res["delivery"]
    rep["stages"] = {ch: runinfo.load_stages(work, ch) for ch, _, _ in specs}  # RUN-3
    # 三立 11/22：交付的 Excel 一律不標黃；標黃副本改為選用（--review-copy），複查請看 review.html 的標題列截圖
    if a.review_copy and not a.no_review_copy:
        review_xlsx = out.with_name(out.stem + "_複查版.xlsx")
        try:
            n = excel_writer.write_review_copy(
                out, review_xlsx, res["evidence"], {k: v["excel_column_header"] for k, v in cfg.items()}, failed,
                {ch: _offset(i, a.sheet_start) for ch, i in clock_info.items()})
            print(f"→ {review_xlsx}（{n} 格標黃並附註解；交件請用上面那份乾淨的）")
        except Exception as e:  # noqa: BLE001 - the review copy is a convenience
            print(f"⚠ 複查版 Excel 沒做成：{e}")
    out.with_suffix(".report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    review.render(res["evidence"], {k: cfg[k]["name"] for k in headers if k in res["cells"]}, out.with_suffix(".review.html"),
                  a.sheet_start, a.minutes, failed={cfg[c]["name"] if c in cfg else c: m for c, m in failed.items()})
    for ch, d in rep["channels"].items():
        flag = "⚠ fallback 過高" if d["alert"] else ""
        print(f"{ch}: {d['cells']} 格，換題 {d['changes']}，廣告 {d['ads']}，低信心 {d['low_conf']} {flag}")
    for ch, ws in warnings.items():
        for w in ws:
            print(f"⚠ [{ch}] {w}")
    for ch, msg in failed.items():
        print(f"✗ 整台失敗：{cfg[ch]['name'] if ch in cfg else ch}（{msg}）— 監看表該台欄位是空的，已列入複查清單")
    for w in res["warnings"]:
        print(f"⚠ {w}")
    d = res["delivery"]
    label = {"READY": "可直接交付", "READY_WITH_REVIEW": "可交付，含待複查項目", "INCOMPLETE": "不完整，不能直接交付"}
    print(f"交付檢查：{label.get(d['status'], d['status'])}（自動通過 {d['auto_accepted']} 格、待複查 {d['review_required']} 格）")
    for p_ in d["problems"]:
        print(f"  ✗ {p_}")
    # 規則 RUN-2：每次執行留一份版本紀錄，每格依據都帶 run_id
    man = runinfo.manifest(
        run_id, started=started.isoformat(timespec="seconds"), finished=_dt.datetime.now().isoformat(timespec="seconds"),
        inputs={ch: metas[ch]["video"] for ch in metas}, template=template, channels_cfg=cfg, rules=rules,
        lexicon_path=lex_path, ocr_backend=a.backend, asr=a.asr, asr_model=a.asr_model, llm_model=llm_model,
        llm_state=llm_state, resumed={ch: {"mode": extraction[ch], "stages": runinfo.load_stages(work, ch)} for ch in extraction},
        out_files=[str(out), str(out.with_suffix(".evidence.jsonl")), str(out.with_suffix(".stories.jsonl")),
                   str(out.with_suffix(".report.json"))])
    man["models"].update(_model_facts(a.backend, rules))
    # 規則 RUN-3：這次執行實際送出的雲端請求（快取命中不算）；只記次數與 API 回報的 token，不估金額
    man["cloud_usage"] = {"label_writing": llm.CLOUD_LABEL_USAGE.snapshot(), "image_rewrite": llm.IMAGE_USAGE.snapshot(),
                          "note": "calls = 實際送出的請求；ok/failed/timeout/empty 為結果；token 為 API 回報值；快取沿用的答案不計"}
    print(f"雲端呼叫：寫標 {man['cloud_usage']['label_writing']}；看圖重寫 {man['cloud_usage']['image_rewrite']}")
    out.with_suffix(".manifest.json").write_text(json.dumps(man, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"→ {out}\n→ {out.with_suffix('.review.html')}\n→ {out.with_suffix('.manifest.json')}（{run_id}）")
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
    # 規則 EVAL-1：只評估標準答案裡有標註的台；沒標的台不算進去（否則每一格都會變成「誤報」）
    scope = [ch for ch, col in gold.items() if col]
    skipped = [ch for ch in sysd if ch not in scope]
    sysd = {ch: v for ch, v in sysd.items() if ch in scope}
    gold = {ch: v for ch, v in gold.items() if ch in scope}
    rng = (a.test_from, a.test_to) if a.test_from else None
    rep = evaluate.evaluate(sysd, gold, a.sheet_start, a.minutes, a.tolerance, rng)
    data = json.loads(rep.to_json())
    data["scope"] = scope
    data["not_annotated"] = skipped
    if a.stories and a.annotation:
        stories = [json.loads(x) for x in Path(a.stories).read_text(encoding="utf-8").splitlines() if x.strip()]
        gold_items = json.loads(Path(a.annotation).read_text(encoding="utf-8"))["items"]
        data["seconds"] = evaluate.seconds_report(stories, gold_items, scope, a.minutes * 60, a.tolerance_sec)
    Path(a.out).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"評估範圍：{'、'.join(scope)}" + (f"（未標註、不計：{'、'.join(skipped)}）" if skipped else ""))
    print(f"換題 F1 {rep.change['f1']}（P {rep.change['precision']} R {rep.change['recall']}）"
          f"｜廣告 F1 {rep.ad['f1']}｜錯誤 {len(rep.errors)} 筆 → {a.out}")
    for ch, d in (data.get("seconds") or {}).items():
        b = d["boundary"]
        print(f"  {ch} 秒級：換題 F1 {b['f1']}（±{b['tolerance_sec']:.0f} 秒，時間差中位數 {b['median_error_sec']} 秒）"
              f"｜切太碎 {d['split']}｜併太多 {d['merge']}｜廣告誤入新聞 {d['ad_leak_sec']} 秒｜新聞誤判廣告 {d['news_lost_to_ad_sec']} 秒")
        for tag, h in d.get("hard_cases", {}).items():
            rec = f"換題找到 {h['boundary_found']}（recall {h['boundary_recall']}）" if h["boundary_recall"] is not None else ""
            ad = f"廣告誤入新聞 {h['ad_leak_sec']}/{h['ad_seconds']} 秒" if h["ad_seconds"] else ""
            print(f"    難例「{tag}」{h['items']} 項：{'；'.join(x for x in (rec, ad) if x)}")
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
    # 規則 QA-1：複查結果寫回每一格的品質狀態，另存一份依據檔（原依據檔不動）
    ev_in = Path(a.evidence) if a.evidence else Path(a.sheet).with_suffix(".evidence.jsonl")
    if ev_in.exists():
        recs = [json.loads(x) for x in ev_in.read_text(encoding="utf-8").splitlines() if x.strip()]
        n = review.apply_review(recs, review.load_corrections(Path(a.corrections)))
        ev_out = Path(a.out).with_suffix(".evidence.jsonl")
        ev_out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs), encoding="utf-8")
        print(f"複查結果：確認 {n['approved']}、修正 {n['corrected']}、退回 {n['rejected']}、仍待複查 {n['still_pending']} → {ev_out}")
    else:
        print(f"⚠ 找不到依據檔 {ev_in}，品質狀態沒有更新（可用 --evidence 指定）")
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
    labels = None
    if a.rules:  # EVAL-1: the answer key uses the same minute rule as the system
        from .schemas import Labels
        labels = Labels.from_rules(_load_yaml(a.rules))
    cells = annotate.gold_cells(data["items"], headers.keys(), a.sheet_start, a.minutes, labels)
    excel_writer.write(Path(a.template), Path(a.out), cells, headers)
    print(f"標準答案（{data['annotator']}）→ {a.out}")
    if a.other:
        from .minute_mapper import minute_labels
        other = annotate.load(Path(a.other))
        oc = annotate.gold_cells(other["items"], headers.keys(), a.sheet_start, a.minutes, labels)
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


def cmd_vision_check(a: argparse.Namespace) -> int:
    """Experimental: re-read low-confidence title crops with a cloud vision model (needs --send)."""
    import json

    from . import vision

    channels = [c.strip() for c in a.channels.split(",") if c.strip()]
    backend = None
    if a.send:  # the key and model are only needed when something is actually sent
        try:
            backend = vision.from_env(a.mode, timeout=a.timeout)
        except vision.VisionConfigError as e:
            raise SystemExit(str(e))
    rep = vision.run_check(Path(a.workdir), channels, backend, send=a.send, max_conf=a.max_conf, limit=a.limit)
    if rep.rows:
        out = Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rep.rows), encoding="utf-8")
        print(f"送出 {rep.sent} 段、失敗 {rep.errors} 段；比對結果 {rep.verdicts} → {out}")
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


def cmd_rules(a: argparse.Namespace) -> int:
    """印出判斷規則總表（src/newsbar/rulebook.py），可另存 Markdown 或 JSON。"""
    from . import rulebook

    if a.markdown:
        Path(a.markdown).parent.mkdir(parents=True, exist_ok=True)
        Path(a.markdown).write_text(rulebook.render_markdown(), encoding="utf-8")
        print(f"→ {a.markdown}")
    if a.json:
        Path(a.json).parent.mkdir(parents=True, exist_ok=True)
        Path(a.json).write_text(rulebook.to_json(), encoding="utf-8")
        print(f"→ {a.json}")
    if not (a.markdown or a.json) or a.print:
        print(rulebook.render_text(), end="")
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

    ru = sub.add_parser("rules", help="印出判斷規則總表（中文），可另存 Markdown 或 JSON")
    ru.add_argument("--markdown", metavar="檔案", help="寫成 Markdown（例：docs/rules.md）")
    ru.add_argument("--json", metavar="檔案", help="寫成 JSON")
    ru.add_argument("--print", action="store_true", help="同時印在終端機（有給檔案時預設不印）")
    ru.set_defaults(func=cmd_rules)

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
    r.add_argument("--video", action="append", help="tvbs=data/tvbs.mp4[@11:59:50]，可重複；有 --input-dir 時可省略")
    r.add_argument("--input-dir", help="一鍵模式：資料夾裡依檔名（台別代號或名稱）找四台影片，沒給 --template 時也在這裡找 .xlsx 範本")
    r.add_argument("--clock", help="所有影片第 0 秒的實際時刻（個別指定用 @）")
    r.add_argument("--template", help="12監看範本 .xlsx；有 --input-dir 時可省略")
    r.add_argument("--out", help="輸出 .xlsx；預設 out/12監看.xlsx")
    r.add_argument("--review-copy", action="store_true", help="另存「_複查版.xlsx」（異常格標黃並附註解的副本；預設不產生，交付檔永遠不標黃）")
    r.add_argument("--no-review-copy", action="store_true", help="（相容舊指令）不另存「_複查版.xlsx」；現在預設就不產生")
    r.add_argument("--lexicon", help="詞庫 yaml（地名、未確認語氣詞、畫面疊字）；預設 config/lexicon.yaml，沒有就用內建")
    r.add_argument("--channels", default="config/channels.example.yaml")
    r.add_argument("--rules", default="config/rules.example.yaml")
    r.add_argument("--workdir", default="data/work")
    r.add_argument("--backend", default="auto", choices=["auto", "vision", "paddle"])
    r.add_argument("--asr", default="faster-whisper", choices=["none", "auto", "mlx", "faster-whisper"],
                   help="語音轉錄；預設 faster-whisper（與網頁版相同），none 只看標題文字")
    r.add_argument("--asr-model", help="例：small（Mac）、large-v3-turbo（GPU）")
    r.add_argument("--asr-parallel", type=int, default=None,
                   help="同時轉錄幾台；預設 Mac 1、其他 2（8 GB 顯示卡），16 GB 以上可設 4")
    r.add_argument("--kb", help="五字標知識庫（RAG）；給了就啟用檢索，否則看 rules 的 labeler.rag.enabled（預設關閉）")
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
    ev.add_argument("--stories", help="系統的 .stories.jsonl：加上 --annotation 才算秒級指標")
    ev.add_argument("--annotation", help="標註 JSON（有秒數），秒級指標用")
    ev.add_argument("--tolerance-sec", type=float, default=30.0, help="秒級換題容許誤差")
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
    ac.add_argument("--evidence", help="原始依據檔；預設與 --sheet 同名的 .evidence.jsonl")
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
    gi.add_argument("--rules", help="系統用的規則檔：標準答案改用同一種分鐘寫法（output.minute_rule）")
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

    vc = sub.add_parser("vision-check", help="（實驗）低信心標題截圖交雲端視覺模型重讀並比對；預設只乾跑，加 --send 才送出")
    vc.add_argument("--workdir", default="data/work", help="跑過 newsbar run 的工作資料夾（要有各台 obs.jsonl 與截圖）")
    vc.add_argument("--channels", default="tvbs,ettv,ftv,cts")
    vc.add_argument("--mode", default="anthropic", choices=["mock", "anthropic", "nvidia"], help="mock＝不連網，只驗證流程；nvidia＝NVIDIA 託管模型（OpenAI 相容格式）")
    vc.add_argument("--max-conf", type=float, default=0.6, help="平均信心低於這個值才處理")
    vc.add_argument("--limit", type=int, help="最多處理幾段（先小量試）")
    vc.add_argument("--timeout", type=float, default=30.0)
    vc.add_argument("--out", default="out/vision_check.jsonl")
    vc.add_argument("--send", action="store_true", help="真的把標題截圖送出去；沒加就只列出會送什麼")
    vc.set_defaults(func=cmd_vision_check)

    from . import kb as kbm
    from . import ml as mlm

    def common(sp, *names):
        d = {"rules": ("--rules", "config/rules.example.yaml"), "gold": ("--gold", "eval/gold/tvbs_researcher_provisional.json"),
             "workdir": ("--workdir", "data/work"), "kb": ("--kb", "data/kb/labels.jsonl"),
             "ml_dir": ("--ml-dir", "data/ml"), "models_dir": ("--models-dir", "data/models")}
        for n in names:
            sp.add_argument(d[n][0], default=d[n][1])

    kbp = sub.add_parser("kb", help="五字標知識庫（RAG，預設關閉）")
    ksub = kbp.add_subparsers(dest="kbcmd", required=True)
    kbb = ksub.add_parser("build", help="由抽查修正與 gold 重建 KB")
    common(kbb, "gold", "workdir", "kb")
    kbb.add_argument("--out-root", default="out/web", help="各 job 資料夾（內含 corrections.json 與 *.evidence.jsonl）")
    kbb.add_argument("--date", default="2026-11-06")
    kbb.add_argument("--no-seed", action="store_true")
    kbb.set_defaults(func=kbm.cmd_build)
    kba = ksub.add_parser("add-sheet", help="匯入歷史監看表（要另附標題檔，只有五字標配不成對）")
    common(kba, "kb")
    kba.add_argument("--sheet", required=True)
    kba.add_argument("--titles", help="標題檔：csv/json/jsonl 欄位 channel,minute,title；或 *.evidence.jsonl")
    kba.add_argument("--channels", default="config/channels.example.yaml")
    kba.add_argument("--date", required=True)
    kba.set_defaults(func=kbm.cmd_add_sheet)
    kbe = ksub.add_parser("eval", help="TVBS gold 逐題排除自己，RAG 關/開對照（需要 Ollama）")
    common(kbe, "rules", "gold", "workdir", "kb")
    kbe.add_argument("-k", type=int, default=3)
    kbe.add_argument("--detail", action="store_true")
    kbe.set_defaults(func=kbm.cmd_eval)

    mlp = sub.add_parser("ml", help="換題與廣告的學習式判斷（預設關閉）")
    msub = mlp.add_subparsers(dest="mlcmd", required=True)
    for name, fn, h in (("dataset", mlm.cmd_dataset, "重建訓練資料 data/ml/*.jsonl"),
                        ("train", mlm.cmd_train, "訓練並存成 data/models/*_vN.json"),
                        ("eval", mlm.cmd_eval, "規則 vs 學習式，並列印各訊號權重"),
                        ("cv", mlm.cmd_cv, "分段交叉驗證（留出一個廣告區塊）"),
                        ("rollback", mlm.cmd_rollback, "退回上一版模型")):
        sp = msub.add_parser(name, help=h)
        common(sp, "rules", "gold", "workdir", "ml_dir", "models_dir")
        sp.set_defaults(func=fn)
        if name in ("train", "eval", "cv"):
            sp.add_argument("--eval-from", default="1220")
            sp.add_argument("--eval-to", default="1259")
        if name in ("train", "cv", "rollback"):
            sp.add_argument("--kind", default="both", choices=["both", "change", "ad"])
        if name in ("train", "cv"):
            sp.add_argument("--l2", type=float, default=1.0)
        if name == "train":
            sp.add_argument("--train-until", default="1220", help="HHMM；只用此時刻之前的資料訓練")
            sp.add_argument("--force", action="store_true", help="留出 F1 下降也取代 active")
            sp.add_argument("--rebuild", action="store_true", help="先重建 data/ml")
        if name == "eval":
            sp.add_argument("--change-model", help="預設用 active")
            sp.add_argument("--ad-model", help="預設用 active")
            sp.add_argument("--tolerance", type=int, default=1)
    a = p.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    raise SystemExit(main())
