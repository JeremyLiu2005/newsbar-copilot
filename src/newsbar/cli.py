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
    a = p.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    raise SystemExit(main())
