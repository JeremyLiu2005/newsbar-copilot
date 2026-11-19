"""Render a recorded run (tools/record_run.py) into an MP4: title, terminal replay, Excel result.

Usage: uv run --with pillow python tools/render_demo_video.py run_log.json excel.png out.mp4
The terminal lines are the run's real output, shown at their real times sped up SPEED times;
the corner clock shows the real elapsed time.
"""

import json
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFont

log, excel_png, out = sys.argv[1:4]
W, H, FPS, SPEED = 1280, 720, 10, 4
FONT = "/System/Library/Fonts/Hiragino Sans GB.ttc"
f_big, f_mid, f_txt, f_small = (ImageFont.truetype(FONT, s) for s in (46, 26, 19, 17))
BG, FG, MUTE, ACC, OK = (18, 22, 29), (226, 230, 236), (140, 150, 165), (232, 160, 78), (108, 196, 149)
lines = json.load(open(log))["lines"]
total = lines[-1][0]

ff = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
                       "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-pix_fmt",
                       "yuv420p", "-crf", "26", out], stdin=subprocess.PIPE)


def emit(img: Image.Image, seconds: float) -> None:
    raw = img.tobytes()
    for _ in range(round(seconds * FPS)):
        ff.stdin.write(raw)


# 1. title
img = Image.new("RGB", (W, H), BG)
d = ImageDraw.Draw(img)
d.text((80, 220), "NewsBar Copilot 實跑錄影", font=f_big, fill=FG)
d.text((80, 300), "四台側錄 2026/11/06 12:30–12:35 片段 → 「12監看」Excel", font=f_mid, fill=FG)
d.text((80, 350), f"下面的終端機畫面是這次執行的真實輸出，依實際時序重播（加速 {SPEED} 倍）", font=f_txt, fill=MUTE)
d.text((80, 382), "執行環境：Mac M4・OCR：Apple Vision・語音：faster-whisper base・五字標：地端 Ollama qwen2.5:7b", font=f_txt, fill=MUTE)
emit(img, 4)

# 2. terminal replay
def wrap(text: str, width: int = 1180) -> list[str]:
    out_, cur = [], ""
    for ch in text:
        if d.textlength(cur + ch, font=f_txt) > width:
            out_.append(cur)
            cur = "  "
        cur += ch
    return out_ + [cur]


frames = int(total / SPEED * FPS) + FPS * 3
for k in range(frames):
    t = k / FPS * SPEED
    shown = [ln for ts, ln in lines if ts <= t]
    rows = [r for ln in shown for r in wrap(ln)][-24:]
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 44], fill=(30, 36, 46))
    d.text((20, 10), "終端機・newsbar run（真實輸出重播）", font=f_small, fill=MUTE)
    el = min(t, total)
    d.text((W - 300, 10), f"實際經過 {int(el // 60):02d}:{int(el % 60):02d}　×{SPEED}", font=f_small, fill=ACC)
    y = 60
    for r in rows:
        col = OK if r.startswith("[完成") or r.startswith("→") else (ACC if "⚠" in r else FG)
        d.text((20, y), r, font=f_txt, fill=col)
        y += 26
    ff.stdin.write(img.tobytes())

# 3. Excel
img = Image.new("RGB", (W, H), (246, 244, 238))
d = ImageDraw.Draw(img)
d.text((60, 50), "產出的「12監看」：以 Microsoft Excel 開啟，未經人工修改", font=f_mid, fill=(20, 33, 61))
x = Image.open(excel_png).convert("RGB")
x = x.resize((1160, round(x.height * 1160 / x.width)))
img.paste(x, (60, 130))
yb = 130 + x.height + 30
for s in ["只寫入四台五字標欄：換題那分鐘填標、延續留白、同分鐘多則用「/」、進廣告填「廣告」",
          "12:30 是片段第一分鐘，正在播的新聞照規則補寫一次標",
          f"四台五分鐘片段共處理 {total:.0f} 秒；整小時四台實測 46 分鐘"]:
    d.text((60, yb), s, font=f_txt, fill=(74, 85, 104))
    yb += 34
emit(img, 7)

ff.stdin.close()
ff.wait()
print("wrote", out)
