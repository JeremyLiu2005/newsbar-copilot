"""Build the S36 submission deck on top of the base .pptx (keeps the cover, draws nine pages that answer the five official questions).

    python3 tools/build_deck.py BASE.pptx OUT.pptx [ASSETS_DIR]

Needs python-pptx and Pillow (pip install python-pptx pillow). ASSETS_DIR defaults to data/deck/assets;
the screenshots and TV frames in it are not in the repo (see docs/deck/README.md).
"""

import sys
from pathlib import Path

from lxml import etree
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LABEL_POSITION, XL_LEGEND_POSITION
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

A = Path(__file__).resolve().parent.parent / "data" / "deck" / "assets"
RED, RED2, ORANGE, INK, MUTE = "B91C1C", "EF4444", "F97316", "171717", "737373"
BG, CARD, BORDER, SOFT, TRACK, SOFTRED, DARK = "FFF9F8", "FFFFFF", "E7D7D4", "F7E9E7", "F3E6E4", "FDE8E6", "7F1D1D"
SOFTOR = "FFEDD5"


def rgb(h):
    return RGBColor.from_string(h)


def text(slide, x, y, w, h, paras, size=14, bold=False, color=INK, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, space=0, line=None):
    """paras: str | list of (str | list of (text, {size,bold,color}))"""
    tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = anchor
    if isinstance(paras, str):
        paras = [paras]
    for i, p in enumerate(paras):
        para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        para.alignment = align
        if space:
            para.space_after = Pt(space)
        if line:
            para.line_spacing = line
        runs = [(p, {})] if isinstance(p, str) else p
        for t, o in runs:
            r = para.add_run()
            r.text = t
            r.font.size = Pt(o.get("size", size))
            r.font.bold = o.get("bold", bold)
            r.font.color.rgb = rgb(o.get("color", color))
    return tb


def box(slide, x, y, w, h, fill=CARD, line=BORDER, radius=0.08, shape=None, lw=1.0):
    kind = shape or (MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE)
    s = slide.shapes.add_shape(kind, Inches(x), Inches(y), Inches(w), Inches(h))
    if kind == MSO_SHAPE.ROUNDED_RECTANGLE:
        s.adjustments[0] = min(0.5, radius / min(w, h)) if min(w, h) else 0
    if fill:
        s.fill.solid()
        s.fill.fore_color.rgb = rgb(fill)
    else:
        s.fill.background()
    if line:
        s.line.color.rgb = rgb(line)
        s.line.width = Pt(lw)
    else:
        s.line.fill.background()
    s.shadow.inherit = False
    return s


def label_box(slide, x, y, w, h, paras, fill=CARD, line=BORDER, size=14, bold=False, color=INK, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE, radius=0.08, pad=0.1, **kw):
    box(slide, x, y, w, h, fill, line, radius, **kw)
    return text(slide, x + pad, y, w - 2 * pad, h, paras, size, bold, color, align, anchor)


def arrow(slide, x1, y1, x2, y2, color=MUTE, width=1.75):
    c = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x1), Inches(y1), Inches(x2), Inches(y2))
    c.line.color.rgb = rgb(color)
    c.line.width = Pt(width)
    ln = c.line._get_or_add_ln()
    tail = etree.SubElement(ln, qn("a:tailEnd"))
    tail.set("type", "triangle")
    tail.set("w", "med")
    tail.set("len", "med")
    return c


def pic(slide, name, x, y, w=None, h=None):
    kw = {}
    if w:
        kw["width"] = Inches(w)
    if h:
        kw["height"] = Inches(h)
    p = slide.shapes.add_picture(str(A / name), Inches(x), Inches(y), **kw)
    p.line.color.rgb = rgb(BORDER)
    p.line.width = Pt(0.75)
    return p


def frame(slide, section, title, num, foot):
    bg = slide.background.fill
    bg.solid()
    bg.fore_color.rgb = rgb(BG)
    box(slide, 0.67, 0.54, 0.56, 0.06, RED, None, 0)
    text(slide, 0.67, 0.69, 5.5, 0.23, section, 11, True, RED)
    text(slide, 0.67, 1.02, 11.35, 0.67, title, 30, True, INK)
    text(slide, 12.08, 0.6, 0.6, 0.27, f"{num:02d}", 14, True, RED)
    text(slide, 0.67, 7.1, 11.9, 0.17, foot, 9, False, MUTE)


def tag(slide, x, y, w, label):
    w = 0.3 + 0.2 * len(label)  # sized to the text, right edge fixed at 11.95
    x = 11.95 - w
    """Question tag (Q1–Q5) on the top right, next to the page number."""
    label_box(slide, x, y, w, 0.27, label, SOFTRED, None, 10, True, RED, radius=0.13, pad=0.04)


def chart_text(chart, size=12):
    chart.font.size = Pt(size)
    chart.font.color.rgb = rgb(INK)


def blank(prs):
    layout = next(l for l in prs.slide_layouts if l.name == "Blank")
    return prs.slides.add_slide(layout)


# ---------------------------------------------------------------- slides
# Ten pages in the order of the UNEXT proposal framework:
# 1 cover · 2 summary · 3 current process (1.1) · 4 root cause and routing (1.2, 1.3) · 5 gap and value (1.4)
# 6 future way of working (2.1) · 7 solution and tools (3.1, 3.3) · 8 solution logic and implementation (3.2, 3.4)
# 9 metrics, test and results (2.2, 2.3, 4.1, 4.2) · 10 plan (5.1–5.3)


def table(s, x, y, colw, rowh, rows, size=11, head=True, hfill=SOFTRED, bold_first=True, hl_rows=()):
    """Simple grid of boxes; rows[0] is the header when head=True."""
    yy = y
    for r, row in enumerate(rows):
        h = rowh[r] if isinstance(rowh, (list, tuple)) else rowh
        xx = x
        for c, cell in enumerate(row):
            is_head = head and r == 0
            fill = hfill if is_head else (SOFTOR if r in hl_rows else CARD)
            box(s, xx, yy, colw[c], h, fill, BORDER, 0, lw=0.75)
            text(s, xx + 0.08, yy, colw[c] - 0.16, h, cell, size, is_head or (bold_first and c == 0),
                 RED if is_head else INK, PP_ALIGN.LEFT, MSO_ANCHOR.MIDDLE)
            xx += colw[c]
        yy += h
    return yy


ROOT = Path(__file__).resolve().parent.parent
CH = [("tvbs", "TVBS-N"), ("ettv", "東森"), ("ftv", "民視"), ("cts", "華視")]


def heat_rows():
    """Per channel, per minute 12:00–12:59 on 11/06: 'c' story change, 'a' ad, '' nothing (system output)."""
    import json
    d = json.loads((ROOT / "demo" / "data.json").read_text())
    g = {}
    for c in d["cells"]:
        g.setdefault(c["ch"], {})[c["m"]] = "a" if c["v"] == "廣告" else ("c" if c["v"] else "")
    mins = [f"12{i:02d}" for i in range(60)]
    return {k: [g.get(k, {}).get(m, "") for m in mins] for k, _ in CH}


def cover_key(s):
    """Which pages answer the five official questions (on the dark cover, right edge)."""
    text(s, 10.5, 1.55, 2.5, 0.3, "官方五問在哪一頁", 13, True, "F97316")
    rows = [("Q1 影片如何自動變成 Excel", "第 3 頁"), ("Q2 換題、字幕與五字標對時", "第 4、5 頁"), ("Q3 廣告包框", "第 6 頁"),
            ("Q4 Excel 呈現與驗證", "第 7、8 頁"), ("Q5 進度與擴大到全天", "第 9、10 頁")]
    y = 2.0
    for a, b in rows:
        text(s, 10.5, y, 2.7, 0.25, a, 12, True, "FFFFFF")
        text(s, 10.5, y + 0.27, 2.5, 0.25, b, 11, False, "FCA5A5")
        y += 0.72


def qtag(s, label):
    tag(s, 0, 0.6, 0, label)


def s_why(prs):
    s = blank(prs)
    frame(s, "背景與痛點", "每分鐘播什麼，要對上每分鐘的收視率", 2, "流程整理自 11/22 說明會；熱力格為 11/06 12:00–12:59 四台系統輸出（三立側錄不納入填表）。")
    steps = [("側錄四台", "民視、TVBS、東森、華視"), ("工讀生逐秒看", "一人盯四台、手打五字標"), ("隔日 9 點", "行政依台別×分鐘灌入收視率"),
             ("對照收視曲線", "0.4 掉到 0.2 是哪則新聞"), ("回饋與跟進", "記者檢討、編播與廣告破口、別家有我們沒有")]
    cw = 2.4
    for i, (n, d) in enumerate(steps):
        x = 0.67 + i * cw
        box(s, x, 1.85, cw - 0.05, 0.45, RED if i == 1 else SOFTRED, None, 0, shape=MSO_SHAPE.PENTAGON)
        text(s, x + 0.15, 1.85, cw - 0.4, 0.45, n, 13, True, "FFFFFF" if i == 1 else RED, PP_ALIGN.LEFT, MSO_ANCHOR.MIDDLE)
        text(s, x + 0.05, 2.36, cw - 0.25, 0.5, d, 11, False, INK)
    text(s, 0.67, 3.0, 9, 0.28, "11/06 午間一小時，四台每分鐘的狀態", 13, True, RED)
    rows = heat_rows()
    gx, gy, cw2, ch = 1.55, 3.36, 0.185, 0.27
    cols = {"c": RED, "a": ORANGE, "": TRACK}
    counts = [0] * 60
    for r, (k, name) in enumerate(CH):
        text(s, 0.67, gy + r * ch, 0.85, ch, name, 10, True, INK, anchor=MSO_ANCHOR.MIDDLE)
        for m, v in enumerate(rows[k]):
            box(s, gx + m * cw2, gy + r * ch + 0.02, cw2 - 0.03, ch - 0.04, cols[v], None, 0)
            counts[m] += v == "c"
    yb = gy + 4 * ch + 0.04
    text(s, 0.67, yb, 0.85, 0.22, "同時換題", 9, True, MUTE, anchor=MSO_ANCHOR.MIDDLE)
    for m, n in enumerate(counts):
        if n >= 2:
            label_box(s, gx + m * cw2, yb, cw2 - 0.03, 0.22, str(n), RED if n >= 3 else SOFTRED, None, 8, True, "FFFFFF" if n >= 3 else RED, radius=0, pad=0)
    for m in range(0, 60, 10):
        text(s, gx + m * cw2, yb + 0.25, 0.6, 0.2, f"12:{m:02d}", 9, False, MUTE)
    for i, (c, t) in enumerate([(RED, "換題"), (ORANGE, "廣告"), (TRACK, "延續")]):
        box(s, 9.9 + i * 0.95, 3.06, 0.16, 0.16, c, None, 0)
        text(s, 10.12 + i * 0.95, 3.0, 0.7, 0.25, t, 10, False, INK)
    stats = [("25 分鐘", "兩台以上在同一分鐘換題"), ("6 分鐘", "三台以上在同一分鐘換題"), ("90 格", "一小時要判斷並寫標的格子"), ("1 分鐘", "錯位，收視掉點就算給錯新聞")]
    for i, (a, b) in enumerate(stats):
        x = 0.67 + i * 3.03
        box(s, x, 5.1, 2.88, 1.1, CARD, BORDER, 0.08)
        text(s, x + 0.2, 5.15, 2.5, 0.55, a, 24, True, RED)
        text(s, x + 0.2, 5.72, 2.55, 0.45, b, 12, False, INK)
    box(s, 0.67, 6.33, 12.0, 0.6, SOFTRED, None, 0.08)
    text(s, 0.87, 6.33, 11.6, 0.6, [[("NewsBar Copilot：", {"bold": True, "color": RED}), ("一鍵把四台側錄填成既有的「12監看」表；每格附判斷依據，系統沒把握的格子自動排給人看。", {})]], 13, False, INK, anchor=MSO_ANCHOR.MIDDLE)
    return s


def s_q1(prs):
    s = blank(prs)
    frame(s, "Q1 影片如何從頭到尾自動變成 Excel", "Q1：一個指令，四台影片進、填好的「12監看」出", 3, "全程在本機執行，影片不離開三立。分 Mac 版與 NVIDIA 顯示卡版，GitHub 兩種版本皆可下載。")
    qtag(s, "Q1")
    box(s, 0.67, 1.85, 12.0, 0.78, CARD, BORDER, 0.08)
    text(s, 0.87, 1.85, 11.6, 0.78, [[("一鍵啟動　", {"bold": True, "color": RED}), ("指令 ", {}), ("newsbar run --input-dir 資料夾", {"bold": True}), ("，自動認出四台影片與範本；或在網頁按「開始分析」", {})],
                                     [("輸出　", {"bold": True, "color": RED}), ("交付版「12監看」（原格式）＋複查版（標黃附註解）＋每格判斷依據；AI 服務沒開會自動啟動，啟動不了改用規則寫標，照樣產出", {})]], 12, False, INK, anchor=MSO_ANCHOR.MIDDLE, space=3)
    arch = [("1 讀影片", "讀畫面角落的電視台時鐘對時；四台各自一路"), ("2 抽畫面與聲音", "每秒 1 張，只裁標題列、時鐘、台徽等固定區域"),
            ("3 辨識", "標題列文字每 5 秒多數決；語音轉文字；聲音指紋"), ("4 先判廣告", "10 種訊號任兩種成立"), ("5 再判換題", "標題換了＋1 項佐證"),
            ("6 寫五字標", "AI 寫，逐字檢查"), ("7 對分鐘寫回", "起始秒所在分鐘；只寫四台五字標欄")]
    w, g = 1.6, 0.13
    for i, (t, d) in enumerate(arch):
        x = 0.67 + i * (w + g)
        last = i == len(arch) - 1
        box(s, x, 2.82, w, 1.3, RED if last else CARD, None if last else BORDER, 0.08)
        text(s, x + 0.08, 2.86, w - 0.16, 1.22, [[(t, {"bold": True, "size": 12})], d], 10, False, "FFFFFF" if last else INK, PP_ALIGN.CENTER, MSO_ANCHOR.MIDDLE, space=3)
        if not last:
            arrow(s, x + w + 0.005, 3.47, x + w + g - 0.005, 3.47, RED, 1.25)
    text(s, 0.67, 4.3, 8, 0.28, "每個工具負責哪一步、怎麼做決定", 13, True, RED)
    rows = [("工具", "負責", "怎麼做決定"),
            ("Apple Vision／PaddleOCR", "讀標題列、時鐘", "只讀各台設定好的標題列位置；同一標題 5 秒內取出現最多次的讀法，單張讀錯不影響"),
            ("faster-whisper", "語音轉文字", "比對換題點前後 20 秒的話題；話題沒變就不算新的一則"),
            ("自建聲音指紋", "認出已知廣告", "確認過的廣告聲音自動入庫；同一支廣告在別台、別天出現直接命中"),
            ("qwen2.5:7b（本機）", "寫五字標", "溫度 0＋結果快取，同一支影片每次結果相同；寫出的字逐一回查標題原文"),
            ("openpyxl", "寫回 Excel", "依表頭文字找到時間列與四台五字標欄，只寫值，其他格子不碰")]
    table(s, 0.67, 4.62, [2.3, 1.7, 8.0], [0.32] + [0.4] * 5, rows, size=11)
    return s


def s_q2a(prs):
    s = blank(prs)
    frame(s, "Q2 換題與字幕", "Q2：只認主標題，鏡位切換不算換題", 4, "畫面為 TVBS-N 11/06 12:04 實際側錄；參數為 11/06 實跑設定（前 20 分鐘調整，之後固定）。")
    qtag(s, "Q2")
    fx, fy, fw = 0.67, 1.9, 5.6
    pic(s, "tvbs_1204_full.jpg", fx, fy, w=fw)
    k = fw / 640
    marks = [((160, 291, 476, 29), RED, "主標題 bar：唯一讀取對象"), ((160, 322, 480, 22), MUTE, "快訊跑馬燈：不採用"),
             ((160, 268, 186, 20), MUTE, "插播字幕、角標：不採用"), ((0, 0, 40, 290), MUTE, "左側標、天氣：不採用"), ((0, 322, 158, 22), ORANGE, "畫面時鐘：用來對時")]
    for (bx, by, bw_, bh_), col, _ in marks:
        box(s, fx + bx * k, fy + by * k, bw_ * k, bh_ * k, None, col, 0, lw=2.0)
    for i, (_, col, t) in enumerate(marks):
        xx, yy = fx + (i % 2) * 2.85, 5.1 + (i // 2) * 0.32
        box(s, xx, yy + 0.06, 0.15, 0.15, col, None, 0)
        text(s, xx + 0.22, yy, 2.6, 0.3, t, 11, False, INK)
    text(s, fx, 6.12, fw, 0.75, "各台標題列、時鐘、台徽的位置設定一次，只讀標題列這一塊；LIVE、直播、獨家等字樣與角落溫度，就算疊在標題列上也會濾掉。", 11, True, INK)
    x, w = 6.55, 6.12
    label_box(s, x, 1.9, w, 0.42, "換題＝標題列換了新標題、穩定 7 秒，再加下列任一佐證", SOFTRED, None, 12, True, RED, radius=0.08, pad=0.05)
    ev = [("語音", "前後 20 秒的話題不同"), ("主播鏡頭", "換題點前後 5 秒出現主播"), ("說話的人", "換了一個人講話"), ("記者名條", "25 秒內閃過新的記者名條")]
    for i, (a, b) in enumerate(ev):
        xx, yy = x + (i % 2) * 3.09, 2.42 + (i // 2) * 0.62
        box(s, xx, yy, 3.0, 0.54, CARD, BORDER, 0.06)
        text(s, xx + 0.12, yy, 2.8, 0.54, [[(a + "　", {"bold": True, "color": RED}), (b, {})]], 11, False, INK, anchor=MSO_ANCHOR.MIDDLE)
    text(s, x, 3.75, w, 0.28, "鏡位切換只是佐證，不會單獨觸發換題", 12, True, RED)
    shots = ["主播稿頭", "SOT 報導", "LIVE 連線", "假連", "說新聞"]
    for i, t in enumerate(shots):
        label_box(s, x + i * 1.22, 4.07, 1.14, 0.4, t, CARD, BORDER, 10, True, INK, pad=0.02)
    box(s, x, 4.52, w, 0.05, MUTE, None, 0)
    text(s, x, 4.6, w, 0.28, "標題列沒換 → 同一則新聞，不論畫面怎麼切", 11, False, MUTE, PP_ALIGN.CENTER)
    text(s, x, 5.0, w, 0.28, "標題換了也不一定是新的一則", 12, True, RED)
    text(s, x, 5.3, w, 1.6, ["3 分鐘內重複出現的標題，併回原本那一則", "沒有任何佐證的新標題（同一則的第二段標題），留在原本那一則",
                             "只閃 5 秒以內的名條、字幕，不當標題", "廣告前不到 20 秒的片段，併進廣告"], 11, False, INK, space=3)
    return s


def s_q2b(prs):
    s = blank(prs)
    frame(s, "Q2 五字標與台別、時間對應", "Q2：五字標每個字有出處，四台各自對準分鐘", 5, "例子皆為 11/06 系統實際產出。表上時間依各台畫面時鐘，不依側錄檔名（兩者差 23–32 秒）。")
    qtag(s, "Q2")
    x, w = 0.67, 5.9
    text(s, x, 1.85, w, 0.28, "五字標怎麼產生", 13, True, RED)
    flow = [("來源", "只用這則新聞的主標題＋語音"), ("AI 寫標", "4–6 字，濃縮人、事、地，不硬切前五字"),
            ("逐字檢查", "每個字出自來源；社會新聞要有地點；標題有「疑、涉、傳」，標也要保留"), ("沒過", "重寫一次；再沒過或 AI 沒回應，改用規則濃縮（保留地點與事件），標黃排進複查")]
    y = 2.2
    for i, (a, b) in enumerate(flow):
        box(s, x, y, w, 0.5, SOFTOR if i == 3 else CARD, BORDER, 0.06)
        text(s, x + 0.15, y, w - 0.3, 0.5, [[(a + "　", {"bold": True, "color": RED}), (b, {})]], 11, False, INK, anchor=MSO_ANCHOR.MIDDLE)
        y += 0.6
    rows = [("台・分鐘", "主標題", "五字標"),
            ("TVBS-N 12:04", "男二度毒駕拒檢…下秒遭車撞飛", "毒駕遭車撞飛"),
            ("民視 12:59", "國三南下苑裡段貨車翻了！大量木材噴飛車道", "苑裡貨車翻車"),
            ("華視 12:35", "武嶺2紅牌重機違停擋道找無車主", "武嶺重機違停"),
            ("TVBS-N 12:29", "涉助共諜案張俊傑偷渡船長.5漁工遭押", "涉助共諜案")]
    table(s, x, 4.68, [1.4, 3.15, 1.35], [0.32, 0.4, 0.4, 0.4, 0.4], rows, size=10.5)
    text(s, x, 6.65, w, 0.3, "保留地點（苑裡、武嶺）與「涉」等未定論語氣；地名表含全台 368 鄉鎮市區。", 10, False, MUTE)
    x, w = 6.8, 5.87
    text(s, x, 1.85, w, 0.28, "秒級判斷，分鐘級填表", 13, True, RED)
    # timeline: ETTV 12:00–12:02
    tx, ty, tw = x + 0.1, 2.45, w - 0.2
    per = tw / 180
    segs = [(-6, 50, "台積電拉回"), (50, 94, "德州新州長"), (94, 107, "0050爆量股"), (107, 159, "陳奕光估反彈"), (159, 180, "台股急跌")]
    fills = [SOFTRED, "FCA5A5", SOFTRED, "FCA5A5", SOFTRED]
    for (a, b, t), f in zip(segs, fills):
        a2 = max(a, 0)
        box(s, tx + a2 * per, ty, (b - a2) * per, 0.42, f, None, 0)
        text(s, tx + a2 * per + 0.03, ty, (b - a2) * per - 0.03, 0.42, t, 9, True, INK, anchor=MSO_ANCHOR.MIDDLE)
    for m in range(4):
        box(s, tx + m * 60 * per, ty - 0.08, 0.02, 0.58, INK, None, 0)
        text(s, tx + m * 60 * per - 0.3, ty - 0.33, 0.8, 0.25, f"12:0{m}:00", 9, True, INK)
    text(s, tx, ty + 0.48, tw, 0.25, "東森 11/06：12:00:50、12:01:34、12:01:47、12:02:39 四次換題", 9, False, MUTE)
    rows = [("台・分鐘", "填入的五字標"), ("東森 12:00", "台積電拉回/德州新州長"), ("東森 12:01", "0050爆量股/陳奕光估反彈"), ("東森 12:02", "台股急跌點（12:02:39 開始）"),
            ("民視 12:01–12:04", "留白：12:00 已填 11:59:46 開始的「車燃起火災」")]
    table(s, x, 3.3, [1.55, 4.32], [0.3, 0.34, 0.34, 0.34, 0.34], rows, size=10.5)
    text(s, x, 5.15, w, 0.28, "填表規則", 12, True, RED)
    text(s, x, 5.45, w, 1.5, ["從哪一秒開始，就填在那一分鐘（12:00:50 → 12:00）", "同一分鐘開始多則，依播出順序用「/」連接", "同一則延續的分鐘留白；12:00 前已開始的那則填在 12:00",
                             "廣告起始那分鐘填「廣告」", "四台各讀自己的畫面時鐘，台別與分鐘不會互相影響"], 10.5, False, INK, space=2)
    return s


def s_q3(prs):
    s = blank(prs)
    frame(s, "Q3 廣告包框如何辨認、記錄與恢復新聞", "Q3：廣告看 10 種訊號，包框裡的舊標題一律不採用", 6, "參數為 11/06 實跑設定。照片：東森 12:34:46–12:38:44 同一段廣告破口，平均截 4 張。")
    qtag(s, "Q3")
    x, w = 0.67, 4.6
    text(s, x, 1.85, w, 0.28, "10 種訊號（任兩種同時成立）", 13, True, RED)
    groups = [("畫面", ["主畫面版面改變（縮成包框）", "台徽消失", "黑畫面切換", "畫面時鐘消失"]), ("標題列", ["底色改變", "出現廣告文案", "輪播今日新聞預告", "跑馬燈消失"]),
              ("聲音", ["廣告用語、沒有主播或記者", "聲音指紋命中已知廣告"])]
    y = 2.2
    for g, items in groups:
        label_box(s, x, y, 0.8, 0.3 * len(items), g, SOFTRED, None, 11, True, RED, radius=0.06, pad=0.02)
        text(s, x + 0.95, y, w - 0.95, 0.3 * len(items), items, 11, False, INK, anchor=MSO_ANCHOR.MIDDLE)
        y += 0.3 * len(items) + 0.12
    text(s, x, 5.55, w, 1.35, ["跑馬燈仍在的台，要多一種訊號才算（新聞圖卡也會擋住台徽）", "確認過的廣告聲音自動入庫，下次同一支廣告在任何一台都直接認出"], 10.5, False, MUTE, space=3)
    x, w = 5.55, 7.12
    text(s, x, 1.85, w, 0.28, "進廣告、記錄、回新聞", 13, True, RED)
    st = [("新聞", ""), ("疑似廣告", "兩種訊號出現"), ("廣告", "持續 10 秒"), ("疑似回新聞", "版面恢復"), ("新聞", "")]
    bw = 1.22
    for i, (n, c) in enumerate(st):
        xx = x + i * (bw + 0.255)
        label_box(s, xx, 2.2, bw, 0.46, n, SOFT if n == "廣告" else CARD, RED if n == "廣告" else BORDER, 12, True, INK, pad=0.02)
        if i < 4:
            arrow(s, xx + bw + 0.01, 2.43, xx + bw + 0.245, 2.43, RED, 1.25)
        if c:
            text(s, xx - 0.25, 2.7, bw, 0.25, c, 9, False, MUTE, PP_ALIGN.CENTER)
    rows = [("階段", "怎麼判斷", "表上怎麼記"),
            ("進廣告", "任兩種訊號同時成立、持續 10 秒；起點是第一個訊號出現的那一秒", "起點那分鐘填「廣告」"),
            ("廣告中", "標題列文字凍結：包框旁的新聞推播、殘留的舊標題都不當換題", "延續留白，不沿用舊標題"),
            ("回新聞", "版面、標題列底色恢復，且出現主播、語音或新標題，維持 5 秒", "回來那分鐘重新填標"),
            ("過濾", "不到 25 秒的不算破口（節目片頭）；8 秒內的空隙併成同一段", "不會多記一格廣告")]
    table(s, x, 3.05, [1.0, 4.27, 1.85], [0.32, 0.5, 0.5, 0.5, 0.5], rows, size=10.5)
    for i, (f, t) in enumerate(zip(["ettv_1234_0_ad05.jpg", "ettv_1234_0_ad35.jpg", "ettv_1234_0_ad65.jpg", "ettv_1234_0_ad95.jpg"], ["12:34:57", "12:36:09", "12:37:20", "12:38:32"])):
        pic(s, f, x + i * 1.8, 5.55, w=1.7)
        text(s, x + i * 1.8, 6.52, 1.7, 0.22, t, 9, True, INK)
    text(s, x, 6.72, w, 0.25, "實測：TVBS-N 一小時 10 個廣告分鐘全部抓到。", 10, True, RED)
    return s


def s_q4a(prs):
    s = blank(prs)
    frame(s, "Q4 Excel 預期結果如何呈現", "Q4：只填四台五字標，收視率欄原封不動", 7, "Excel 為 11/06 實際產出節錄（12:05 到 12:34 之間省略）。三立側錄僅供參考，不填表。")
    qtag(s, "Q4")
    text(s, 0.67, 1.85, 7, 0.28, "產出的「12監看」（紅框＝換題那分鐘）", 13, True, RED)
    pic(s, "excel_annot.png", 0.67, 2.18, w=6.9)
    x, w = 7.85, 4.82
    text(s, x, 1.85, w, 0.28, "怎麼保住原表", 13, True, RED)
    text(s, x, 2.18, w, 1.9, ["只開「12監看」分頁，依表頭文字找到時間列與四台五字標欄", "只寫儲存格的值；A30、M30、A4+ 收視率欄不碰，隔天照常灌入",
                             "不新增秒級列或欄位；已有內容的格子不覆寫", "寫完逐格比對：其他格子的值、格式、條件格式、凍結窗格都和原表一樣才存檔"], 11, False, INK, space=4)
    text(s, x, 4.2, w, 0.28, "另外附上", 13, True, RED)
    text(s, x, 4.5, w, 0.9, ["複查版 Excel：同一張表，沒把握的格子標黃，註解寫影片時間、標題原文、語音、原因", "每格判斷依據檔與複查清單"], 11, False, INK, space=3)
    box(s, 0.67, 5.6, 12.0, 1.35, SOFT, None, 0.08)
    text(s, 0.87, 5.65, 4, 0.28, "不確定怎麼辦：不停、照填、標黃排給人看", 12, True, RED)
    fb = [("標題讀不到", "改用語音決定名稱"), ("AI 沒過或沒回應", "改用規則濃縮"), ("換題佐證不足", "照常填寫"), ("某一台出狀況", "其他三台照跑，失敗寫進報告")]
    for i, (a, b) in enumerate(fb):
        xx = 0.87 + i * 2.95
        label_box(s, xx, 6.0, 2.8, 0.75, [[(a, {"bold": True, "size": 11})], [("→ " + b, {"size": 11})]], CARD, BORDER, 11, False, INK, pad=0.05)
    return s


def s_q4b(prs):
    s = blank(prs)
    frame(s, "Q4 如何驗證", "Q4：逐分鐘對答案，每一格都能回到影片", 8, "對照答案：TVBS-N 午間一小時人工標註，逐分鐘比對；前 20 分鐘調整規則，12:20 之後只測、不再修改。")
    qtag(s, "Q4")
    rows = [("指標", "怎麼算", "目標", "實測"),
            ("換題準確度", "和人工答案比，起始分鐘差 1 分鐘內算對", "0.85", "0.885"),
            ("廣告準確度", "同上，以分鐘計", "0.90", "0.952"),
            ("自動填寫率", "90 格中由系統自動填的比例", "100%", "100%"),
            ("處理時間", "四台一小時影片處理完", "隔日 9 點前", "46 分鐘")]
    table(s, 0.67, 1.9, [1.45, 3.65, 1.1, 1.0], [0.34, 0.42, 0.42, 0.42, 0.42], rows, size=11)
    cd = CategoryChartData()
    cd.categories = ["換題準確度", "廣告準確度"]
    cd.add_series("實測", (0.885, 0.952))
    cd.add_series("目標", (0.85, 0.90))
    gf = s.shapes.add_chart(XL_CHART_TYPE.BAR_CLUSTERED, Inches(8.3), Inches(2.15), Inches(4.37), Inches(1.95), cd)
    ch = gf.chart
    chart_text(ch, 10)
    ch.has_legend = True
    ch.legend.position = XL_LEGEND_POSITION.BOTTOM
    ch.legend.include_in_layout = False
    ch.legend.font.size = Pt(10)
    for ser, c in zip(ch.plots[0].series, (RED, "D4D4D4")):
        ser.format.fill.solid()
        ser.format.fill.fore_color.rgb = rgb(c)
    pl = ch.plots[0]
    pl.gap_width = 60
    pl.has_data_labels = True
    pl.data_labels.number_format = "0.000"
    pl.data_labels.number_format_is_linked = False
    pl.data_labels.font.size = Pt(10)
    va = ch.value_axis
    va.minimum_scale, va.maximum_scale = 0.7, 1.0
    va.visible = False
    va.has_major_gridlines = False
    ch.category_axis.tick_labels.font.size = Pt(10)
    ch.category_axis.reverse_order = True
    text(s, 8.3, 1.85, 4.3, 0.28, "準確度：實測對目標", 12, True, RED)
    for i, (a, b) in enumerate([("23／27", "換題點找到"), ("10／10", "廣告分鐘全找到"), ("0.758 → 0.885", "加上語音後的換題準確度")]):
        xx = 0.67 + i * 2.5
        box(s, xx, 4.2, 2.38, 0.95, CARD, BORDER, 0.06)
        text(s, xx + 0.12, 4.22, 2.2, 0.5, a, 18, True, RED)
        text(s, xx + 0.12, 4.7, 2.2, 0.4, b, 10, False, INK)
    text(s, 0.67, 5.35, 7.3, 0.28, "可回頭比對：每格附當下畫面與依據", 13, True, RED)
    text(s, 0.67, 5.67, 7.3, 1.3, ["點任一格，看到換題那一秒的畫面、標題原文、語音節錄與判斷依據", "按「對、改寫、不該填」一鍵判定；改正另存修正版，原表不動",
                                   "修正紀錄每週用來更新規則，同樣情況下次就判對"], 11, False, INK, space=3)
    pic(s, "rv_card.png", 8.3, 4.2, h=2.75)
    for i, (f, t) in enumerate([("qr_proto.png", "流程示範"), ("qr_monitor.png", "結果網頁")]):
        qy = 4.2 + i * 1.4
        s.shapes.add_picture(str(A / f), Inches(11.75), Inches(qy), width=Inches(0.92))
        text(s, 11.6, qy + 0.93, 1.2, 0.22, t, 9, False, INK, PP_ALIGN.CENTER)
    return s


def s_q5(prs):
    s = blank(prs)
    frame(s, "Q5 目前進度與擴大到全天", "Q5：一台 Mac 已跟得上全天四台", 9, "處理時間為一台 Mac（M4）快速模式實測。換算假設全天 24 小時、四台都要處理；顯示卡版程式已備，尚待實機測速。")
    qtag(s, "Q5")
    text(s, 0.67, 1.85, 4, 0.28, "已經做到", 13, True, RED)
    done = ["一鍵全自動：四台影片 → 填好的「12監看」", "網頁操作：選影片、看進度、看結果", "複查台：每格附畫面與依據，修正另存新版",
            "複查版 Excel、全天自動處理、每日品質日報", "Mac 版與 NVIDIA 顯示卡版，GitHub 皆可下載"]
    for i, t in enumerate(done):
        y = 2.2 + i * 0.46
        label_box(s, 0.67, y + 0.06, 0.28, 0.28, "✓", RED, None, 11, True, "FFFFFF", radius=0.14, pad=0)
        text(s, 1.05, y, 3.7, 0.42, t, 11, False, INK, anchor=MSO_ANCHOR.MIDDLE)
    x = 5.0
    text(s, x, 1.85, 4, 0.28, "擴大到全天的速度", 13, True, RED)
    cd = CategoryChartData()
    cd.categories = ["影片長度", "Mac 處理時間"]
    cd.add_series("分鐘", (60, 46))
    gf = s.shapes.add_chart(XL_CHART_TYPE.BAR_CLUSTERED, Inches(x), Inches(2.15), Inches(3.6), Inches(1.4), cd)
    ch = gf.chart
    ch.has_title = False
    chart_text(ch, 10)
    ch.has_legend = False
    pl = ch.plots[0]
    pl.gap_width = 40
    pl.has_data_labels = True
    pl.data_labels.font.size = Pt(10)
    pl.series[0].format.fill.solid()
    pl.series[0].format.fill.fore_color.rgb = rgb(RED)
    pt = pl.series[0].points[0]
    pt.format.fill.solid()
    pt.format.fill.fore_color.rgb = rgb("D4D4D4")
    va = ch.value_axis
    va.minimum_scale, va.maximum_scale = 0, 70
    va.visible = False
    va.has_major_gridlines = False
    ch.category_axis.reverse_order = True
    ch.category_axis.tick_labels.font.size = Pt(10)
    text(s, x, 3.6, 3.6, 0.9, ["每處理 1 小時四台影片只要 46 分鐘", "全天 24 小時四台 ≈ 一台 Mac 每天 18.4 小時，跟得上"], 10.5, False, INK, space=3)
    x = 8.85
    text(s, x, 1.85, 3.8, 0.28, "成本與換時段、換頻道", 13, True, RED)
    text(s, x, 2.2, 3.8, 2.3, ["全部本機執行：不用雲端 API，不按次計費", "成本只有一台機器與電費", "換時段（下午 4 點、晚間 6 點）：指定開始時間與分鐘數", "換頻道：用校正頁框一次標題列、時鐘、台徽位置",
                               "收視率隔日 9 點才灌入，時間很充裕"], 11, False, INK, space=4)
    text(s, 0.67, 4.65, 8, 0.28, "擴大到全天，還需要三立提供", 13, True, RED)
    need = [("側錄自動交檔", "每小時切檔，自動排入處理"), ("四台人工答案", "兩位監看員各標一小時，當驗收標準"), ("兩種記法", "廣告後接回同一則、節目預告怎麼記"), ("一台伺服器", "顯示卡機，四台同時處理")]
    for i, (a, b) in enumerate(need):
        xx = 0.67 + i * 3.03
        box(s, xx, 5.0, 2.88, 0.85, CARD, BORDER, 0.06)
        text(s, xx + 0.15, 5.0, 2.6, 0.85, [[(a, {"bold": True, "color": RED, "size": 12})], b], 10.5, False, INK, anchor=MSO_ANCHOR.MIDDLE, space=2)
    rows = [("Day 0–30", "四台答案、統一記法"), ("Day 31–60", "午間和人工並行比較"), ("Day 61–90", "全天四台交付")]
    for i, (a, b) in enumerate(rows):
        xx = 0.67 + i * 4.06
        box(s, xx, 6.05, 3.92, 0.5, RED if i == 2 else SOFTRED, None, 0, shape=MSO_SHAPE.PENTAGON)
        text(s, xx + 0.15, 6.05, 3.6, 0.5, [[(a + "　", {"bold": True}), (b, {})]], 11, False, "FFFFFF" if i == 2 else RED, anchor=MSO_ANCHOR.MIDDLE)
    text(s, 0.67, 6.62, 12, 0.3, "每階段達標才進下一階段；準確度低於 0.70 就停止擴大，回頭調整規則。", 10, True, MUTE)
    return s


def s_adopt(prs):
    s = blank(prs)
    frame(s, "導入後的工作方式與效益", "導入後：五步完成一小時，對齊企業五項指標", 10, "畫面為操作原型。價值為假設，待業師確認：每日監看時數 × 365 天 × 系統接手 85% × 時薪 NT$235 − 維運 22 萬；時薪參考 2026 年基本時薪 196 元。")
    for x, w, t in [(0.67, 2.0, "1 選影片　2 分析"), (2.8, 2.4, "3 看結果"), (5.33, 1.3, "4 複查")]:
        label_box(s, x, 1.85, w, 0.34, t, RED, None, 11, True, "FFFFFF", radius=0.17, pad=0.03)
    pic(s, "upload.png", 0.67, 2.28, w=2.0)
    pic(s, "progress.png", 0.67, 4.08, w=2.0)
    pic(s, "result.png", 2.8, 2.28, w=2.4)
    pic(s, "rv_queue.png", 5.33, 2.28, h=2.95)
    text(s, 2.8, 4.2, 2.4, 1.2, "人不再逐秒盯四台，只看系統排出的格子（約 3 成），最終決定權在人。", 11, False, INK)
    text(s, 0.67, 5.4, 6, 0.28, "一年的價值（依每日監看時數）", 12, True, RED)
    rows = [("每日監看", "4 小時", "8 小時", "18 小時"), ("人力價值", "29 萬", "58 萬", "131 萬"), ("扣維運後", "7 萬", "36 萬", "109 萬")]
    table(s, 0.67, 5.7, [1.5, 1.5, 1.5, 1.5], [0.36, 0.4, 0.4], rows, size=11)
    x, w = 7.0, 5.67
    text(s, x, 1.85, w, 0.28, "企業五項落地指標", 13, True, RED)
    crit = [("準確", "換題 0.885、廣告 0.952；四台各讀自己的時鐘，台別與分鐘不錯位", "第 5、8 頁"),
            ("穩定", "10 種訊號認廣告包框；鏡位切換不算換題；跑馬燈與左側標不讀", "第 4、6 頁"),
            ("可交付", "直接寫進原「12監看」，收視率欄與格式原封不動，存檔前逐格比對", "第 7 頁"),
            ("可驗證", "複查版 Excel 標黃附註解；每格可看當秒畫面與依據，一鍵判定", "第 7、8 頁"),
            ("可擴充", "一台 Mac 跟得上全天四台；換時段指定時間、換頻道框一次位置", "第 9 頁")]
    y = 2.2
    for a, b, c in crit:
        box(s, x, y, w, 0.88, CARD, BORDER, 0.06)
        label_box(s, x + 0.12, y + 0.2, 0.95, 0.48, a, SOFTRED, None, 13, True, RED, radius=0.08, pad=0.02)
        text(s, x + 1.2, y, 3.55, 0.88, b, 10.5, False, INK, anchor=MSO_ANCHOR.MIDDLE)
        text(s, x + 4.8, y, 0.8, 0.88, c, 9, True, MUTE, anchor=MSO_ANCHOR.MIDDLE)
        y += 0.96
    return s


# ---------------------------------------------------------------- assemble
def drop_slide(prs, idx):
    lst = prs.slides._sldIdLst
    sld = list(lst)[idx]
    prs.part.drop_rel(sld.rId)
    lst.remove(sld)


def main(base, out, assets=None):
    global A
    if assets:
        A = Path(assets)
    prs = Presentation(base)
    for i in range(len(prs.slides) - 1, 0, -1):  # keep only the base cover
        drop_slide(prs, i)
    cover_key(prs.slides[0])
    for fn in (s_why, s_q1, s_q2a, s_q2b, s_q3, s_q4a, s_q4b, s_q5, s_adopt):
        fn(prs)
    prs.save(out)
    print("slides:", len(prs.slides))


if __name__ == "__main__":
    main(*sys.argv[1:4])
