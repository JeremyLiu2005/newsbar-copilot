"""Review console, demo form (FR-15, FR-16, NFR-06): one static HTML file.

Four channels side by side on a minute timeline; cells to check first
(fallback → low confidence → ad edges) are listed at the top with the bar
crop, the source text and the triggering signals. Corrections are typed into
the page and exported as JSON (the correction log), never written back into
the delivered sheet.
"""

from __future__ import annotations

import base64
import html
import json
from pathlib import Path

from .evidence import review_priority
from .minute_mapper import minute_labels
from .schemas import ErrorType

PRIO_NAME = {0: "fallback", 1: "低信心", 2: "廣告邊界", 3: "其他"}


def _img(path: str | None, inline: bool) -> str:
    if not path:
        return ""
    p = Path(path)
    if inline and p.exists():
        b64 = base64.b64encode(p.read_bytes()).decode()
        return f'<img src="data:image/jpeg;base64,{b64}" alt="main bar">'
    return f'<img src="{html.escape(p.as_uri() if p.is_absolute() else str(p))}" alt="main bar">'


def render(evidence: list[dict], channel_names: dict[str, str], out: Path,
           start: str = "1200", n_minutes: int = 60, inline_images: bool = True, top: int = 40) -> Path:
    minutes = minute_labels(start, n_minutes)
    grid = {(r["channel"], r["minute"]): r for r in evidence}
    queue = sorted(evidence, key=lambda r: (review_priority(r), r["minute"]))[:top]
    errs = [e.value for e in ErrorType]
    e = html.escape

    rows = []
    for m in minutes:
        tds = []
        for ch in channel_names:
            r = grid.get((ch, m))
            if r:
                cls = ["c", f"p{review_priority(r)}"]
                tds.append(f'<td class="{" ".join(cls)}" title="{e(" ".join(r["signals"]))}">{e(r["value"])}</td>')
            else:
                tds.append("<td></td>")
        rows.append(f"<tr><th>{m}</th>{''.join(tds)}</tr>")

    cards = []
    for i, r in enumerate(queue):
        it = r["items"][0] if r["items"] else {}
        opts = "".join(f"<option>{e(x)}</option>" for x in errs)
        cards.append(f"""
<article class="card p{review_priority(r)}" data-key="{e(r['channel'])}|{e(r['minute'])}">
  <header><b>{e(channel_names.get(r['channel'], r['channel']))}</b> {e(r['minute'])}
    <span class="tag">{PRIO_NAME[review_priority(r)]}</span>
    <span class="val">{e(r['value'])}</span></header>
  {_img(it.get('frame'), inline_images)}
  <p class="src">bar：{e(it.get('bar_text') or '—')}<br>語音：{e((it.get('asr_text') or '—')[:120])}</p>
  <p class="sig">依據：{e(' '.join(r['signals']) or '—')}｜信心：{e(r['confidence'])}｜fallback：{e(r['fallback'] or '—')}</p>
  <div class="act">
    <label><input type="radio" name="v{i}" value="ok"> 對</label>
    <label><input type="radio" name="v{i}" value="fix"> 錯，改成 <input class="fix" placeholder="正確寫法"></label>
    <select class="etype"><option value="">錯誤類型</option>{opts}</select>
  </div>
</article>""")

    head = "".join(f"<th>{e(n)}</th>" for n in channel_names.values())
    page = f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>監看表抽查</title>
<style>
:root{{--bg:#fff;--fg:#1a1a1a;--mute:#666;--line:#ddd;--p0:#ffe0e0;--p1:#fff1cc;--p2:#e2ecff}}
@media (prefers-color-scheme:dark){{:root{{--bg:#16181c;--fg:#e8e8e8;--mute:#9a9a9a;--line:#333;--p0:#4a2222;--p1:#4a3d1a;--p2:#1e2d4a}}}}
body{{margin:0;padding:16px;background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,"PingFang TC",sans-serif}}
main{{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.3fr);gap:24px}}
@media (max-width:900px){{main{{grid-template-columns:1fr}}}}
table{{border-collapse:collapse;width:100%}} th,td{{border:1px solid var(--line);padding:2px 6px;text-align:left}}
td.c{{font-weight:600}} .p0{{background:var(--p0)}} .p1{{background:var(--p1)}} .p2{{background:var(--p2)}}
.card{{border:1px solid var(--line);border-radius:8px;padding:10px;margin-bottom:12px}}
.card img{{max-width:100%;display:block;margin:6px 0;border-radius:4px}}
.tag{{font-size:12px;color:var(--mute);margin-left:8px}} .val{{float:right;font-size:16px;font-weight:700}}
.src,.sig{{margin:4px 0;color:var(--mute)}} .act{{display:flex;flex-wrap:wrap;gap:12px;align-items:center}}
button{{padding:6px 14px;font-size:14px}}
</style></head><body>
<h1>監看表抽查</h1>
<p>右側依優先序列出最值得檢查的 {len(queue)} 格（紅：fallback、黃：低信心、藍：廣告邊界）。修正只寫進修正紀錄，不改動已交付的監看表。</p>
<button id="export">匯出修正紀錄 JSON</button>
<main><section><table><tr><th>時間</th>{head}</tr>{''.join(rows)}</table></section>
<section>{''.join(cards)}</section></main>
<script>
document.getElementById('export').onclick=()=>{{
  const out=[...document.querySelectorAll('.card')].map(c=>{{
    const v=c.querySelector('input[type=radio]:checked');
    const [channel,minute]=c.dataset.key.split('|');
    return v?{{channel,minute,verdict:v.value,correct:c.querySelector('.fix').value||null,
      error_type:c.querySelector('.etype').value||null,reviewed_at:new Date().toISOString()}}:null;
  }}).filter(Boolean);
  const a=document.createElement('a');
  a.href=URL.createObjectURL(new Blob([JSON.stringify(out,null,2)],{{type:'application/json'}}));
  a.download='corrections.json';a.click();
}};
</script></body></html>"""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    return out


def apply_corrections(cells: dict[str, dict[str, str]], corrections: list[dict]) -> dict[str, dict[str, str]]:
    """FR-16: build the corrected copy; the original stays untouched."""
    fixed = {ch: dict(v) for ch, v in cells.items()}
    for c in corrections:
        if c.get("verdict") == "fix" and c.get("correct") is not None:
            fixed.setdefault(c["channel"], {})[c["minute"]] = c["correct"]
    return fixed


def load_corrections(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))
