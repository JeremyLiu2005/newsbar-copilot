"""Gold-standard annotation helper (requirements §7 step 3; FR-23).

Watching four channels for an hour to write a gold sheet is the slow part of
evaluation. This page lists every distinct main-bar text that stayed ≥3 s
(titles, subtitles and credits alike — the annotator decides) with its time,
plus where the bar disappeared. The annotator ticks "new story" / "ad starts"
and types a label; the export (JSON) is turned into a 12監看-shaped gold
sheet by `newsbar gold-import`. Two annotators work independently; Cohen's κ
on the ticked minutes is computed by `kappa()`.

The page shows no system decisions, so the gold is not anchored to the output
being evaluated.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

from .schemas import SecondObs


def runs(obs: list[SecondObs], min_sec: int = 3) -> list[dict]:
    out: list[dict] = []
    cur = None
    for o in sorted(obs, key=lambda o: o.sec):
        t = o.bar_text or "（主 bar 無字）"
        if cur and cur["text"] == t:
            cur["end"] = o.sec
            continue
        if cur and cur["end"] - cur["start"] + 1 >= min_sec:
            out.append(cur)
        cur = {"start": o.sec, "end": o.sec, "text": t}
    if cur and cur["end"] - cur["start"] + 1 >= min_sec:
        out.append(cur)
    return out


def clock(sec: int, sheet_start: str = "1200") -> str:
    t = int(sheet_start[:2]) * 3600 + int(sheet_start[2:]) * 60 + sec
    return f"{t // 3600:02d}:{t % 3600 // 60:02d}:{t % 60:02d}"


def render(streams: dict[str, list[SecondObs]], names: dict[str, str], out: Path, sheet_start: str = "1200") -> Path:
    e = html.escape
    sections = []
    for ch, obs in streams.items():
        rows = "".join(
            f'<tr data-sec="{r["start"]}"><td>{clock(r["start"], sheet_start)}</td><td>{r["end"] - r["start"] + 1}s</td>'
            f'<td>{e(r["text"])}</td><td><select><option value="">—</option><option value="news">新的一則</option>'
            f'<option value="ad">廣告開始</option><option value="weather">氣象</option></select></td>'
            f'<td><input placeholder="五字標"></td></tr>'
            for r in runs(obs))
        sections.append(f'<section data-ch="{e(ch)}"><h2>{e(names.get(ch, ch))}</h2>'
                        f'<table><tr><th>時間</th><th>長度</th><th>主 bar 文字</th><th>標記</th><th>五字標</th></tr>{rows}</table></section>')
    page = f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><title>監看標註</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{{font:14px/1.5 system-ui,"PingFang TC",sans-serif;padding:16px;background:#fff;color:#1a1a1a}}
table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #ddd;padding:2px 6px;text-align:left}}
td:nth-child(3){{max-width:40em}} input{{width:7em}}</style></head><body>
<h1>監看標註（標準答案）</h1>
<p>依側錄影片判斷：每則新聞開始的那一行選「新的一則」並填五字標；廣告開始選「廣告開始」。字幕與名條不用標。兩位標註者請各自獨立完成。</p>
<label>標註者 <input id="who"></label> <button id="exp">匯出 JSON</button>
{''.join(sections)}
<script>
document.getElementById('exp').onclick=()=>{{const who=document.getElementById('who').value.trim();
 if(!who){{alert('請填標註者');return;}}
 const out={{annotator:who,items:[]}};
 document.querySelectorAll('section').forEach(s=>s.querySelectorAll('tr[data-sec]').forEach(r=>{{
  const k=r.querySelector('select').value;if(!k)return;
  out.items.push({{channel:s.dataset.ch,sec:+r.dataset.sec,kind:k,label:r.querySelector('input').value.trim()}});}}));
 const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(out,null,2)],{{type:'application/json'}}));
 a.download='gold_'+who+'.json';a.click();}};
</script></body></html>"""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    return out


def gold_cells(items: list[dict], channels, start: str = "1200", n_minutes: int = 60, labels=None):
    """Annotation JSON → minute cells in the 12監看 notation (via the same mapper and, with `labels`,
    the same output.minute_rule as the system — EVAL-1: otherwise a "next"-rule system is compared
    with a "floor"-rule answer key and every ad edge looks one minute off)."""
    from .minute_mapper import map_events
    from .schemas import Event, EventType

    evs = []
    for i, it in enumerate(items):
        kind = {"news": EventType.NEWS, "ad": EventType.AD, "weather": EventType.WEATHER}[it["kind"]]
        label = (it.get("label") or f"未命名{i}") if kind is EventType.NEWS else None
        evs.append(Event(it["channel"], it["sec"], kind, label, f"g{i}"))
    return map_events(evs, channels, start, n_minutes, labels) if labels is not None else map_events(evs, channels, start, n_minutes)


def kappa(a: dict[str, set[str]], b: dict[str, set[str]], minutes: list[str]) -> float:
    """Cohen's κ on 'a story starts in this minute' over channel × minute."""
    pairs = [(m in a.get(ch, set()), m in b.get(ch, set())) for ch in set(a) | set(b) for m in minutes]
    n = len(pairs)
    if not n:
        return 1.0
    po = sum(x == y for x, y in pairs) / n
    pa, pb = sum(x for x, _ in pairs) / n, sum(y for _, y in pairs) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return 1.0 if pe == 1 else (po - pe) / (1 - pe)


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
