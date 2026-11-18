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
    a = p.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    raise SystemExit(main())
