"""Segmentation-only score on TVBS vs the provisional gold (no LLM, seconds)."""
from pathlib import Path
from newsbar.annotate import gold_cells, load
from newsbar.ad_state import detect
from newsbar.evaluate import evaluate
from newsbar.extract import load_obs
from newsbar.minute_mapper import map_events
from newsbar.schemas import Event, EventType
from newsbar.segmenter import segment
import sys
obs = load_obs(Path("data/work_full/tvbs/obs.jsonl"))
if "--no-asr" in sys.argv:
    for o in obs:
        o.asr_text = o.topic = o.speaker = None
        o.ad_speech = False
segs = segment(obs, detect(obs))
evs = [Event("tvbs", s.start, s.type, (s.bar_texts[0][:5] if s.bar_texts else f"x{i}") if s.type is EventType.NEWS else None, f"s{i}") for i, s in enumerate(segs)]
for e in evs:
    if e.type is EventType.NEWS:
        e.label = e.label.replace("/", "")
sysd = {"tvbs": {m: c.value for m, c in map_events(evs, ["tvbs"])["tvbs"].items()}}
g = load(Path("eval/gold/tvbs_researcher_provisional.json"))
gold = {"tvbs": {m: c.value for m, c in gold_cells(g["items"], ["tvbs"])["tvbs"].items()}}
r = evaluate(sysd, gold)
print("換題", r.change, "\n廣告", r.ad)
t = evaluate(sysd, gold, only_minutes=("1220", "1259"))
print("測試段 12:20–12:59 換題", t.change["f1"], "廣告", t.ad["f1"])
for e in r.errors: print(" ", e)
