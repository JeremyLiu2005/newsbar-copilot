"""Collect the 11/06 run into demo/ (data.json + main-bar crops) for the web demo.

Usage: uv run --with pillow python tools/build_demo.py
Reads out/12監看_20261106.* and data/work_full/<ch>/obs.jsonl; writes demo/data.json and demo/img/.
"""

import json
from pathlib import Path

import yaml
from PIL import Image

from newsbar.ad_state import AdParams, detect
from newsbar.annotate import gold_cells, load
from newsbar.evaluate import evaluate, read_sheet
from newsbar.extract import load_obs

OUT = Path("demo")
CH = {"tvbs": "TVBS-N", "ettv": "東森", "ftv": "民視", "cts": "華視"}
rules = yaml.safe_load(open("config/rules.1106.yaml"))

(OUT / "img").mkdir(parents=True, exist_ok=True)


def crop(src: str, name: str) -> str | None:
    p = Path(src)
    if not p.exists():
        return None
    im = Image.open(p).convert("RGB")
    if im.width > 640:
        im = im.resize((640, round(im.height * 640 / im.width)))
    im.save(OUT / "img" / name, "JPEG", quality=70)
    return f"img/{name}"


cells = []
for line in open("out/12監看_20261106.evidence.jsonl"):
    d = json.loads(line)
    items = []
    for k, it in enumerate(d["items"]):
        img = crop(it["frame"], f"{d['channel']}_{d['minute']}_{k}.jpg") if it.get("frame") else None
        items.append({
            "type": it["type"], "label": it.get("label"), "start": it.get("start_sec"),
            "bar": it.get("bar_text"), "asr": (it.get("asr_text") or "")[:160] or None, "img": img,
        })
    cells.append({
        "ch": d["channel"], "m": d["minute"], "v": d["value"], "items": items,
        "sig": d.get("signals", []), "conf": d.get("confidence"), "fb": d.get("fallback"),
    })

timeline = {}
for ch in CH:
    obs = load_obs(Path(f"data/work_full/{ch}/obs.jsonl"))
    res = detect(obs, AdParams.from_rules(rules))
    runs, cur, start = [], None, None
    for o in sorted(obs, key=lambda o: o.sec):
        s = res.states.get(o.sec, "news")
        if s != cur:
            if cur is not None:
                runs.append([cur, start, o.sec])
            cur, start = s, o.sec
    runs.append([cur, start, obs[-1].sec + 1])
    timeline[ch] = runs

g = load(Path("eval/gold/tvbs_researcher_provisional.json"))
gold = {m: c.value for m, c in gold_cells(g["items"], ["tvbs"])["tvbs"].items()}
sysd = {"tvbs": read_sheet("out/12監看_20261106.xlsx", {"tvbs": "TVBS-N"})["tvbs"]}
full = evaluate(sysd, {"tvbs": gold})
test = evaluate(sysd, {"tvbs": gold}, only_minutes=("1220", "1259"))

report = json.load(open("out/12監看_20261106.report.json"))
data = {
    "channels": CH,
    "cells": cells,
    "timeline": timeline,
    "gold": {"tvbs": gold},
    "errors": full.errors,
    "metrics": {"change": full.change, "ad": full.ad, "change_test": test.change, "ad_test": test.ad},
    "report": report["channels"],
}
json.dump(data, open(OUT / "data.json", "w"), ensure_ascii=False)
print(len(cells), "cells;", sum(1 for c in cells for i in c["items"] if i["img"]), "crops;",
      {k: len(v) for k, v in timeline.items()}, "runs")
