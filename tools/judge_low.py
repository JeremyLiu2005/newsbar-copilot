"""Run the LLM second opinion (FB-1 changes, experimental FB-4 labels) over an evidence file.

Usage: uv run python tools/judge_low.py --evidence out/12監看_20261106.evidence.jsonl --out out/judge_low.jsonl
"""

import argparse
import json

from newsbar import judge

ap = argparse.ArgumentParser()
ap.add_argument("--evidence", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--host", default="http://127.0.0.1:11435")
ap.add_argument("--model", default="qwen2.5:7b-instruct")
a = ap.parse_args()
rows = [json.loads(line) for line in open(a.evidence, encoding="utf-8")]
ask = judge.ollama_ask(a.host, a.model)
res = [{"kind": "FB-1", **r} for r in judge.judge_changes(rows, ask)] + \
      [{"kind": "FB-4", **r} for r in judge.judge_labels(rows, ask)]
with open(a.out, "w", encoding="utf-8") as f:
    for r in res:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")
print(len(res), "checked")
