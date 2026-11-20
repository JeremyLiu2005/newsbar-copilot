"""LLM second opinion on low-confidence cells (suggestions for the human reviewer only).

FB-1 (story change with weak signals): is the next on-screen title a new story, the same
story, or not a headline at all?  Measured on the 11/06 tvbs sample: the verdicts matched the
provisional gold on all 4 FB-1 cells.  FB-4 (rule-extracted label) is experimental: the local
7B model flags almost everything and passed a truncated label, so it is not wired into the UI.

Nothing here writes the sheet.  Runs against the local Ollama server only (NFR-14).
"""

from __future__ import annotations

import json
import urllib.request
from typing import Callable

Ask = Callable[[str, str], dict]

CHANGE_SYS = (
    "你是新聞監看品管。給你前一則與後一則的主標題，判斷後一則是否為新的一則新聞："
    "same＝同一則（換句話說、補充、同一事件的不同角度）；different＝不同新聞；"
    "noise＝後一段不是新聞標題（亂碼、廣告字、無意義短句）。"
    "只輸出 JSON：{\"verdict\":\"same\"或\"different\"或\"noise\",\"reason\":\"一句話\"}"
)
LABEL_SYS = (
    "你是新聞監看品管。給你一則新聞的主標題與一個五字標，判斷五字標是否能讓人認出是哪則新聞："
    "人名、地點、事件、結果要對應主標題；只有截斷、缺關鍵詞、或與標題不符才判 fix。"
    "只輸出 JSON：{\"verdict\":\"ok\"或\"fix\",\"better\":\"建議五字標，ok 時留空\",\"reason\":\"一句話\"}"
)
VERDICTS = {"same", "different", "noise"}


def ollama_ask(host: str, model: str, system: str | None = None, timeout: float = 120.0) -> Ask:
    """Return ask(system, user) -> parsed JSON object, deterministic (temperature 0, seed 42)."""
    def ask(sys_prompt: str, user: str) -> dict:
        body = {"model": model, "stream": False, "format": "json", "options": {"temperature": 0, "seed": 42},
                "messages": [{"role": "system", "content": sys_prompt}, {"role": "user", "content": user}]}
        req = urllib.request.Request(f"{host.rstrip('/')}/api/chat", json.dumps(body).encode(),
                                     {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(json.loads(r.read())["message"]["content"])
    return ask


def news_by_channel(evidence: list[dict]) -> dict[str, list[tuple[int, str]]]:
    """Every read title per channel in time order, so the previous story is found across cells."""
    seq: dict[str, dict[int, str]] = {}
    for d in evidence:
        for it in d["items"]:
            if it["type"] == "news" and it.get("bar_text") and it.get("start_sec") is not None:
                seq.setdefault(d["channel"], {})[it["start_sec"]] = it["bar_text"]
    return {ch: sorted(v.items()) for ch, v in seq.items()}


def judge_changes(evidence: list[dict], ask: Ask) -> list[dict]:
    """One verdict per news item in a cell flagged FB-1.  An item with no earlier title on its
    channel (first story of the hour) gets verdict "first" and no model call."""
    timeline = news_by_channel(evidence)
    out = []
    for d in evidence:
        if "FB-1" not in (d.get("fallback") or ""):
            continue
        for it in d["items"]:
            if it["type"] != "news":
                continue
            start, cur = it.get("start_sec") or 0, it.get("bar_text") or ""
            prev = next((t for sec, t in reversed(timeline.get(d["channel"], [])) if sec < start), None)
            row = {"channel": d["channel"], "minute": d["minute"], "start": it.get("start_sec"), "prev": prev, "bar": cur}
            if not prev or not cur:
                out.append({**row, "verdict": "first", "reason": "沒有前一則標題可比對"})
                continue
            v = ask(CHANGE_SYS, f"前一則：{prev}\n後一則：{cur}")
            verdict = v.get("verdict") if v.get("verdict") in VERDICTS else "unknown"
            out.append({**row, "verdict": verdict, "reason": str(v.get("reason") or "")[:120]})
    return out


def judge_labels(evidence: list[dict], ask: Ask) -> list[dict]:
    """Experimental FB-4 check (see module docstring); not used by the server."""
    out = []
    for d in evidence:
        if "FB-4" not in (d.get("fallback") or ""):
            continue
        for it in d["items"]:
            if it["type"] == "news" and it.get("bar_text"):
                v = ask(LABEL_SYS, f"主標題：{it['bar_text']}\n五字標：{it.get('label')}")
                out.append({"channel": d["channel"], "minute": d["minute"], "start": it.get("start_sec"),
                            "bar": it["bar_text"], "label": it.get("label"), **v})
    return out
