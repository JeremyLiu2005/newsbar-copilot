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


import subprocess
import re

OBS = {ch: load_obs(Path(f"data/work_full/{ch}/obs.jsonl")) for ch in CH}
IDX = {ch: {o.sec: int(re.search(r"(\d+)\.jpg$", o.frame).group(1)) for o in obs if o.frame} for ch, obs in OBS.items()}


def pick_second(ch: str, it: dict, end: int) -> int:
    """The second to show: the title steadily on the bar, not the switch-over second.
    For an ad, a few seconds into the break."""
    s = it.get("start_sec") or 0
    if it["type"] == "ad":
        return min(s + 8, end - 1)
    want = (it.get("bar_text") or "").strip()
    obs = [o for o in OBS[ch] if s <= o.sec < end]
    for k, o in enumerate(obs):  # first second where the read title holds for 3 s
        win = obs[k:k + 3]
        if len(win) == 3 and all(w.bar_text and w.bar_text == o.bar_text for w in win) and (not want or o.bar_text == want):
            return win[1].sec
    for o in obs:
        if o.bar_text and o.bar_text == want:
            return o.sec
    return s


def frame_at(ch: str, sec: int, name: str) -> str | None:
    idx = IDX[ch].get(sec)
    if idx is None:
        return None
    out = OUT / "img" / f"{name}.jpg"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", str(idx + 0.5), "-i", f"data/raw/{ch}.ts",
                    "-frames:v", "1", "-vf", "scale=640:-2", "-q:v", "5", str(out)], check=True)
    return f"img/{out.name}"


def shots(ch: str, sec: int, name: str) -> tuple[str | None, str | None]:
    idx = IDX[ch].get(sec)
    if idx is None:
        return None, None
    bar = Path(f"data/work_full/{ch}/frames/main_bar/{idx:05d}.jpg")
    bar_out = None
    if bar.exists():
        im = Image.open(bar).convert("RGB")
        if im.width > 640:
            im = im.resize((640, round(im.height * 640 / im.width)))
        im.save(OUT / "img" / f"{name}_bar.jpg", "JPEG", quality=72)
        bar_out = f"img/{name}_bar.jpg"
    full = OUT / "img" / f"{name}_full.jpg"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", str(idx + 0.5), "-i", f"data/raw/{ch}.ts",
                    "-frames:v", "1", "-vf", "scale=640:-2", "-q:v", "5", str(full)], check=True)
    return bar_out, f"img/{full.name}"


evid = [json.loads(line) for line in open("out/12監看_20261106.evidence.jsonl")]
starts = {ch: sorted(it.get("start_sec") or 0 for d in evid if d["channel"] == ch for it in d["items"]) for ch in CH}
cells = []
for d in evid:
    ch = d["channel"]
    items = []
    for k, it in enumerate(d["items"]):
        s = it.get("start_sec") or 0
        end = next((x for x in starts[ch] if x > s), 3600)
        sec = pick_second(ch, it, end)
        bar, full = shots(ch, sec, f"{ch}_{d['minute']}_{k}") if it["type"] != "ad" else (None, None)
        strip = []
        if it["type"] == "ad":  # an ad break gets four pictures spread across it, not one
            bar = None
            for f in (0.05, 0.35, 0.65, 0.95):
                t = int(s + (end - s) * f)
                if (src := frame_at(ch, t, f"{ch}_{d['minute']}_{k}_ad{int(f * 100):02d}")):
                    strip.append({"sec": t, "src": src})
        items.append({
            "type": it["type"], "label": it.get("label"), "start": s, "end": end, "shot": sec,
            "bar": it.get("bar_text"), "asr": (it.get("asr_text") or "")[:160] or None, "img": bar, "full": full,
            "strip": strip,
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
print(len(cells), "cells;", sum(1 for c in cells for i in c["items"] if i["full"]), "full frames;",
      {k: len(v) for k, v in timeline.items()}, "runs")
