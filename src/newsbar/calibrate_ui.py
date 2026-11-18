"""Graphical ROI calibration (FR-17): one self-contained HTML page.

Shows a full frame; the operator drags a box for each region (main_bar,
ticker, clock, logo, main_video, framed_roi, anchor_roi) and copies the
generated YAML into channels.yaml. Coordinates are relative (0–1).
"""

from __future__ import annotations

import base64
import html
import json
from pathlib import Path

REGIONS = ["main_bar", "ticker", "clock", "logo", "main_video", "framed_roi", "anchor_roi"]
COLORS = ["#e53935", "#fb8c00", "#43a047", "#1e88e5", "#8e24aa", "#00897b", "#6d4c41"]


def render(frame_png: Path, channel: str, existing: dict | None, out: Path) -> Path:
    b64 = base64.b64encode(frame_png.read_bytes()).decode()
    init = {k: v for k, v in (existing or {}).items() if k in REGIONS}
    buttons = "".join(
        f'<button data-r="{r}" style="border-color:{c}">{r}</button>' for r, c in zip(REGIONS, COLORS))
    page = f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>ROI 校正 {html.escape(channel)}</title>
<style>
body{{margin:0;padding:16px;font:14px system-ui,"PingFang TC",sans-serif;background:#fff;color:#1a1a1a}}
#wrap{{position:relative;display:inline-block;max-width:100%}} #img{{max-width:100%;display:block;user-select:none}}
.box{{position:absolute;border:2px solid;pointer-events:none}} .box span{{background:#fff;font-size:11px;padding:0 3px}}
button{{margin:2px;padding:4px 10px;border:2px solid;background:#fff;cursor:pointer}} button.on{{background:#222;color:#fff}}
pre{{background:#f4f4f4;padding:8px;overflow:auto}}
</style></head><body>
<h1>ROI 校正：{html.escape(channel)}</h1>
<p>先按區域名稱，再在畫面上拖曳框選；可重複修改。完成後複製下方 YAML 到 channels.yaml。</p>
<div>{buttons}</div>
<div id="wrap"><img id="img" src="data:image/png;base64,{b64}" draggable="false"></div>
<pre id="yaml"></pre>
<script>
const regions={json.dumps(init)};const colors={json.dumps(dict(zip(REGIONS, COLORS)))};
let cur=null,start=null;const wrap=document.getElementById('wrap'),img=document.getElementById('img');
document.querySelectorAll('button').forEach(b=>b.onclick=()=>{{cur=b.dataset.r;
  document.querySelectorAll('button').forEach(x=>x.classList.toggle('on',x===b));}});
const rel=e=>{{const r=img.getBoundingClientRect();return [Math.min(1,Math.max(0,(e.clientX-r.left)/r.width)),
  Math.min(1,Math.max(0,(e.clientY-r.top)/r.height))];}};
img.onmousedown=e=>{{if(!cur)return;start=rel(e);e.preventDefault();}};
window.onmouseup=e=>{{if(!start)return;const p=rel(e);
  const x=Math.min(start[0],p[0]),y=Math.min(start[1],p[1]),w=Math.abs(p[0]-start[0]),h=Math.abs(p[1]-start[1]);
  if(w>0.005&&h>0.005)regions[cur]=[x,y,w,h].map(v=>Math.round(v*1000)/1000);start=null;draw();}};
function draw(){{wrap.querySelectorAll('.box').forEach(b=>b.remove());
  for(const[k,v]of Object.entries(regions)){{const d=document.createElement('div');d.className='box';
    d.style.cssText=`left:${{v[0]*100}}%;top:${{v[1]*100}}%;width:${{v[2]*100}}%;height:${{v[3]*100}}%;border-color:${{colors[k]}}`;
    d.innerHTML=`<span>${{k}}</span>`;wrap.appendChild(d);}}
  document.getElementById('yaml').textContent='  {html.escape(channel)}:\\n'+Object.entries(regions).map(([k,v])=>`    ${{k}}: [${{v.join(', ')}}]`).join('\\n');}}
draw();
</script></body></html>"""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    return out
