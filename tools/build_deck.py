"""Build the S36 submission deck on top of the base .pptx (keeps the cover, draws the ten framework pages in the same style).

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
    """Which pages answer the five questions of the challenge (on the dark cover, right edge)."""
    text(s, 10.5, 1.55, 2.5, 0.3, "五個問題在哪一頁", 13, True, "F97316")
    rows = [("影片到 Excel 全自動（Q1）", "第 7、8 頁"), ("換題、字幕與對時（Q2）", "第 4、8 頁"), ("廣告包框（Q3）", "第 8 頁"),
            ("Excel 與驗證（Q4）", "第 6、9 頁"), ("進度與擴大（Q5）", "第 9、10 頁")]
    y = 2.0
    for a, b in rows:
        text(s, 10.5, y, 2.6, 0.25, a, 12, True, "FFFFFF")
        text(s, 10.5, y + 0.27, 2.5, 0.25, b, 11, False, "FCA5A5")
        y += 0.72


def s_summary(prs):
    s = blank(prs)
    frame(s, "摘要", "一個人盯不過四台：系統全填，人只複查", 2, "數字來自 11/06 12:00–13:00 四台實測；價值為假設值（每日 18 小時監看、時薪 NT$235、維運 22 萬），待業師確認。")
    rows = [
        ("問題痛點與價值", "一個人同時盯四台：11/06 午間一小時有 25 分鐘是兩台以上同一分鐘換題，廣告包框又留著舊標題，人眼來不及，造成漏記與時間錯位。導入後每年約可釋出 5,585 小時。",
         "NT$109 萬", "每年淨效益（假設）"),
        ("核心解決方案", "NewsBar Copilot：讀標題列文字、語音、畫面與聲音 10 種訊號，先判廣告、再判換題，由本機 AI 寫五字標並逐字檢查，自動填回既有「12監看」表；人只看系統排出的格子。三立新聞部主導、AI 轉型辦公室協助。",
         "90 格", "一小時四台全自動填完"),
        ("實測結果", "11/06 四台一小時：換題準確度 0.885（目標 0.85）、廣告準確度 0.952（目標 0.90），廣告分鐘 10／10 全找到；90 格全部自動填完；一台 Mac 46 分鐘處理完四台，顯示卡並行版已備好。",
         "0.952", "廣告準確度（目標 0.90）"),
    ]
    y = 1.95
    for i, (a, b, v, c) in enumerate(rows):
        box(s, 0.67, y, 12.0, 1.55, CARD, BORDER, 0.08)
        label_box(s, 0.87, y + 0.5, 2.0, 0.55, a, SOFTRED, None, 14, True, RED, radius=0.1, pad=0.05)
        text(s, 3.1, y + 0.12, 6.6, 1.3, b, 13, False, INK, anchor=MSO_ANCHOR.MIDDLE, line=1.12)
        text(s, 9.9, y + 0.2, 2.6, 0.7, v, 26, True, ORANGE if i == 1 else RED, PP_ALIGN.CENTER, MSO_ANCHOR.MIDDLE)
        text(s, 9.9, y + 0.9, 2.6, 0.4, c, 11, False, MUTE, PP_ALIGN.CENTER)
        y += 1.7
    return s


def s_current(prs):
    s = blank(prs)
    frame(s, "1.1 現況分析", "現況：一個人逐分鐘盯四台、手填", 3, "流程整理自課程題目與 10/26 歷史範例；熱力格為 11/06 12:00–12:59 四台系統輸出。各節點工時沒有公開資料，以每日 18 小時監看假設，待業師確認。")
    tag(s, 0, 0.6, 0, "換題與字幕（Q2）")
    steps = [("看四台畫面", "四台側錄", "一人同時盯四路"), ("判斷換題或廣告", "眼睛與經驗", "多台同一分鐘換題"),
             ("寫五字標", "手打", "要讓人認得是哪則"), ("填入分鐘格", "Excel「12監看」", "漏記、時間對不上"), ("隔日貼收視率", "Excel（行政）", "錯一分鐘就算錯新聞")]
    cw = 2.4
    for i, (n, tool, pain) in enumerate(steps):
        x = 0.67 + i * cw
        box(s, x, 1.85, cw - 0.05, 0.45, RED if i == 1 else SOFTRED, None, 0, shape=MSO_SHAPE.PENTAGON)
        text(s, x + 0.15, 1.85, cw - 0.4, 0.45, n, 13, True, "FFFFFF" if i == 1 else RED, PP_ALIGN.LEFT, MSO_ANCHOR.MIDDLE)
        box(s, x, 2.38, cw - 0.12, 0.82, CARD, RED if i == 1 else BORDER, 0.06, lw=1.5 if i == 1 else 0.75)
        text(s, x + 0.12, 2.42, cw - 0.35, 0.78, [[("工具　", {"color": MUTE}), (tool, {})], [("痛點　", {"color": MUTE}), (pain, {"bold": True})]], 11, False, INK, anchor=MSO_ANCHOR.MIDDLE, space=2)
    text(s, 0.67, 3.27, 12, 0.25, "負責人：前四步是當班監看人員；隔日貼收視率是行政人員。填表規則：換題那分鐘填標、延續留白、同分鐘多則用「/」、廣告起始那分鐘填「廣告」。", 10, False, MUTE)
    text(s, 0.67, 3.62, 9, 0.28, "11/06 午間一小時，四台每分鐘的狀態", 13, True, RED)
    rows = heat_rows()
    gx, gy, cw2, ch = 1.55, 3.98, 0.185, 0.27
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
        box(s, 9.9 + i * 0.95, 3.68, 0.16, 0.16, c, None, 0)
        text(s, 10.12 + i * 0.95, 3.62, 0.7, 0.25, t, 10, False, INK)
    stats = [("25 分鐘", "兩台以上在同一分鐘換題"), ("6 分鐘", "三台以上在同一分鐘換題"), ("90 格", "一小時要判斷並寫標的格子"), ("1 分鐘", "錯位，收視高點就算給錯新聞")]
    for i, (a, b) in enumerate(stats):
        x = 0.67 + i * 3.03
        box(s, x, 5.65, 2.88, 1.25, CARD, BORDER, 0.08)
        text(s, x + 0.2, 5.72, 2.5, 0.6, a, 24, True, RED)
        text(s, x + 0.2, 6.32, 2.55, 0.5, b, 12, False, INK)
    return s


def s_cause(prs):
    s = blank(prs)
    frame(s, "1.2 真因分析・1.3 路由判斷", "真因：要同時盯好幾種訊號，人眼做不到", 4, "魚骨圖整理自題目說明與 10/26 範例；畫面為 TVBS 11/06 12:04 實際側錄。路由判斷先問「能不能不用 AI 解決」。")
    tag(s, 0, 0.6, 0, "換題與字幕（Q2）")
    cats = [("人", ["一人同時盯四台", "多台同時換題"]), ("畫面", ["包框殘留舊標題", "跑馬燈、名條干擾"]),
            ("規則", ["同一標題反覆出現", "廣告後接回怎麼記"]), ("流程", ["逐分鐘手填", "隔日才發現錯"])]
    for i, (cat, items) in enumerate(cats):
        x, y = 0.67 + (i % 2) * 2.12, 1.9 + (i // 2) * 1.22
        box(s, x, y, 2.02, 1.12, CARD, BORDER, 0.08)
        text(s, x + 0.12, y + 0.06, 1.8, 0.28, cat, 13, True, RED)
        text(s, x + 0.12, y + 0.4, 1.85, 0.7, items, 11, False, INK, space=2)
    arrow(s, 4.85, 2.96, 5.1, 2.96, RED, 2.0)
    box(s, 5.13, 1.9, 2.3, 2.34, SOFTRED, None, 0.08)
    text(s, 5.28, 1.95, 2.05, 2.25, [[("表面現象", {"bold": True, "color": RED})], "漏記、時間錯位", [("實際根因", {"bold": True, "color": RED})],
                                    "換題與廣告要同時比對標題文字、語音、版面好幾種訊號；四台一起來，人眼跟不上。"], 12, False, INK, anchor=MSO_ANCHOR.MIDDLE, space=3)
    fx, fy, fw = 7.62, 1.9, 3.6
    pic(s, "tvbs_1204_full.jpg", fx, fy, w=fw)
    k = fw / 640
    marks = [((160, 291, 476, 29), RED, "標題列：只讀這裡"), ((160, 322, 480, 22), MUTE, "跑馬燈：不採用"),
             ((160, 268, 186, 20), MUTE, "插播字幕：不採用"), ((0, 322, 158, 22), ORANGE, "畫面時鐘：對時用")]
    for (bx, by, bw_, bh_), col, _ in marks:
        box(s, fx + bx * k, fy + by * k, bw_ * k, bh_ * k, None, col, 0, lw=2.0)
    for i, (_, col, t) in enumerate(marks):
        box(s, 11.35, 2.0 + i * 0.5, 0.15, 0.15, col, None, 0)
        text(s, 11.55, 1.93 + i * 0.5, 1.2, 0.45, t, 10, False, INK)
    text(s, fx, 3.98, 5.0, 0.25, "同一個畫面有 4 種文字，只有標題列是新聞標題", 10, True, MUTE)
    text(s, 0.67, 4.42, 6, 0.28, "路由判斷：每個真因用什麼處理", 13, True, RED)
    rows = [("真因", "處理方式", "理由"),
            ("多台同時換題，一人看不完", "AI 導入", "加人只能分攤，判斷仍靠反應速度；機器四台同時看"),
            ("包框殘字、跑馬燈與字幕干擾", "AI 導入＋規則", "要同時比對多種畫面訊號，規則寫清楚，交給機器最穩"),
            ("記法沒統一（廣告後接回、節目預告）", "管理制度", "不需要 AI，請業師訂統一記法"),
            ("擔心 AI 出錯或取代人", "團隊溝通", "人保留最終決定權，系統只排出要看的格子")]
    table(s, 0.67, 4.75, [3.6, 1.8, 6.6], [0.36, 0.44, 0.44, 0.44, 0.44], rows, size=12)
    return s


def s_gap(prs):
    s = blank(prs)
    frame(s, "1.4 改善空間與商業價值", "改善空間：每年約 5,585 小時，淨效益約 NT$109 萬", 5, "價值為假設，待業師確認：每日 18 小時 × 365 天 × 系統接手 85% × 時薪 NT$235。時薪參考：2026 年基本時薪 196 元、2027 年預定 205 元（勞動部）。")
    rows = [("項目", "現況", "導入後", "改善"),
            ("人工監看工時（每日）", "18 小時（假設）", "約 2.7 小時", "減少 85%"),
            ("人工要看的格子", "每台每分鐘都要看", "只看系統排出的格子", "約 3 成"),
            ("四台一小時的判斷與填表", "人即時盯 4 路畫面", "系統自動完成", "人不必即時盯"),
            ("每年人力價值", "131 萬", "維運約 22 萬", "淨省 109 萬")]
    table(s, 0.67, 1.95, [2.4, 1.9, 1.9, 1.3], 0.5, rows, size=12)
    cd = CategoryChartData()
    cd.categories = ["人力價值", "維運成本", "淨效益"]
    cd.add_series("base", (0, 109, 0))
    cd.add_series("萬元", (131, 22, 109))
    gf = s.shapes.add_chart(XL_CHART_TYPE.COLUMN_STACKED, Inches(8.45), Inches(2.2), Inches(4.2), Inches(2.4), cd)
    ch = gf.chart
    chart_text(ch, 11)
    ch.has_legend = False
    plot = ch.plots[0]
    plot.gap_width = 55
    base, val = plot.series[0], plot.series[1]
    base.format.fill.background()
    base.format.line.fill.background()
    for i, (c, lab) in enumerate([(RED, "+131 萬"), (MUTE, "−22 萬"), (ORANGE, "+109 萬")]):
        pt = val.points[i]
        pt.format.fill.solid()
        pt.format.fill.fore_color.rgb = rgb(c)
        dl = pt.data_label
        dl.position = XL_LABEL_POSITION.INSIDE_END
        dl.text_frame.text = lab
        r = dl.text_frame.paragraphs[0].runs[0]
        r.font.size = Pt(12)
        r.font.bold = True
        r.font.color.rgb = rgb("FFFFFF")
    va = ch.value_axis
    va.minimum_scale, va.maximum_scale = 0, 140
    va.major_unit = 35
    va.has_major_gridlines = True
    va.major_gridlines.format.line.color.rgb = rgb(BORDER)
    va.format.line.fill.background()
    va.tick_labels.font.size = Pt(10)
    ch.category_axis.tick_labels.font.size = Pt(11)
    text(s, 8.45, 1.9, 4.2, 0.28, "一年的價值（萬元，假設值）", 12, True, RED)
    text(s, 0.67, 4.75, 8, 0.28, "維持現狀一年的風險", 13, True, RED)
    risks = [("下游分析失準", "漏記或錯一分鐘，隔日收視與廣告破口分析會把收視高點算給錯的新聞。", "衝擊：高"),
             ("人力持續被佔用", "每年約 131 萬元的人力花在逐分鐘填表；台數或時段增加，成本跟著增加。", "衝擊：中"),
             ("尖峰品質不穩", "多台同時換題只能靠反應速度，疲勞時更容易漏記。", "衝擊：中")]
    for i, (a, b, c) in enumerate(risks):
        x = 0.67 + i * 4.06
        box(s, x, 5.1, 3.86, 1.65, CARD, BORDER, 0.08)
        text(s, x + 0.18, 5.18, 2.4, 0.3, a, 13, True, INK)
        label_box(s, x + 2.65, 5.2, 1.05, 0.28, c, SOFTRED, None, 10, True, RED, radius=0.14, pad=0.02)
        text(s, x + 0.18, 5.6, 3.5, 1.1, b, 13, False, INK)
    return s


def s_future(prs):
    s = blank(prs)
    frame(s, "2.1 未來的工作方式", "未來：五步完成一小時，人只看系統排出的格子", 6, "畫面為操作原型，結果是 11/06 實跑輸出；複查紀錄為示範。負責人與檢查週期為提案設定，待業師確認。")
    tag(s, 0, 0.6, 0, "Excel 與驗證（Q4）")
    for x, w, t in [(0.67, 2.3, "1 選影片　2 分析"), (3.12, 2.9, "3 看結果"), (6.17, 4.29, "4 複查　5 存修正版")]:
        label_box(s, x, 1.85, w, 0.36, t, RED, None, 12, True, "FFFFFF", radius=0.18, pad=0.05)
    pic(s, "upload.png", 0.67, 2.3, w=2.3)
    pic(s, "progress.png", 0.67, 4.4, w=2.3)
    text(s, 0.67, 5.78, 2.3, 0.9, "選四台影片與空白範本；四台同時跑，各自顯示進度。", 11, False, INK)
    pic(s, "result.png", 3.12, 2.3, w=2.9)
    text(s, 3.12, 4.6, 2.9, 1.5, ["逐段列出新聞與廣告、每格的五字標與依據。", "系統沒把握的格子自動標出，排進複查清單。"], 11, False, INK, space=4)
    pic(s, "rv_queue.png", 6.17, 2.3, h=3.35)
    pic(s, "rv_card.png", 7.74, 2.3, h=3.35)
    text(s, 6.17, 5.78, 4.29, 0.9, "每格附當下畫面、標題與語音；按「對、改寫、不該填」一鍵判定。改正另存修正版，原表不動。", 11, False, INK)
    x, w = 10.68, 2.0
    blocks = [("人機分工", ["系統：90 格全填，每格留依據", "人：只看系統排出的格子", "最終決定權在人"]),
              ("沒把握時的三條退路", ["標題讀不到：改用語音命名", "AI 標兩次沒過：從標題摘字", "佐證不足：照常填，排進複查"]),
              ("誰負責", ["新聞部窗口每天看品質日報", "每週依修正紀錄更新規則"])]
    y = 1.85
    for h, items in blocks:
        text(s, x, y, w, 0.28, h, 12, True, RED)
        text(s, x, y + 0.3, w, 0.3 * len(items) + 0.25, items, 10, False, INK, space=2)
        y += 0.4 + 0.3 * len(items) + 0.3
    return s


def s_solution(prs):
    s = blank(prs)
    frame(s, "3.1 解決方案・3.3 採用工具", "架構：四台側錄進，填好的「12監看」出", 7, "全程在本機執行，影片不離開三立。分 Mac 版與 NVIDIA 顯示卡版，GitHub 兩種版本皆可下載。")
    tag(s, 0, 0.6, 0, "全自動流程（Q1）・Excel（Q4）")
    box(s, 0.67, 1.85, 5.9, 0.9, CARD, BORDER, 0.08)
    text(s, 0.85, 1.85, 5.6, 0.9, [[("輸入　", {"bold": True, "color": RED}), ("四台側錄影片（.ts／.mp4）＋空白「12監看」範本", {})],
                                    [("輸出　", {"bold": True, "color": RED}), ("填好的「12監看」＋每格判斷依據＋複查清單", {})]], 12, False, INK, anchor=MSO_ANCHOR.MIDDLE, space=3)
    box(s, 6.77, 1.85, 5.9, 0.9, SOFT, None, 0.08)
    text(s, 6.95, 1.85, 5.6, 0.9, [[("不做的事　", {"bold": True, "color": RED}), ("不填收視率欄、不新增欄位、不做秒級紀錄、", {})],
                                    "不判斷新聞真偽或立場、不取代既有側錄系統"], 12, False, INK, anchor=MSO_ANCHOR.MIDDLE, space=3)
    arch = [("側錄影片", "四台各一路"), ("抽畫面與音軌", "每秒 1 張，裁出標題列、跑馬燈、時鐘、台徽"), ("辨識", "文字、語音、聲音指紋、版面變化"),
            ("判斷", "先判廣告，再判換題"), ("AI 寫標", "本機模型，逐字檢查"), ("寫回 Excel", "只寫四台五字標欄")]
    ws = [1.45, 2.4, 2.3, 1.75, 1.75, 1.6]
    x = 0.67
    for i, ((t, d), w) in enumerate(zip(arch, ws)):
        last = i == len(arch) - 1
        box(s, x, 2.95, w, 1.0, RED if last else CARD, None if last else BORDER, 0.08)
        text(s, x + 0.08, 3.0, w - 0.16, 0.9, [[(t, {"bold": True, "size": 13})], d], 10, False, "FFFFFF" if last else INK, PP_ALIGN.CENTER, MSO_ANCHOR.MIDDLE, space=2)
        if not last:
            arrow(s, x + w + 0.01, 3.45, x + w + 0.15, 3.45, RED, 1.25)
        x += w + 0.16
    rows = [("功能", "選用", "怎麼做", "解決什麼"),
            ("文字辨識", "Apple Vision／PaddleOCR", "只讀標題列；每 5 秒取出現最多次的讀法", "跑馬燈、名條干擾，單張讀錯"),
            ("畫面時鐘對時", "同上", "讀角落的電視台時鐘，秒換算成分鐘格", "填在對的分鐘"),
            ("語音轉錄", "faster-whisper", "比對前後 20 秒的話題，判斷是不是新的一則", "只看標題分不出同一則或新一則"),
            ("聲音指紋", "自建（300 Hz–2 kHz，33 個頻段）", "確認過的廣告自動入庫，下次同一支直接認出", "包框廣告畫面還留著新聞"),
            ("AI 寫標", "qwen2.5:7b（本機）", "溫度 0＋結果快取，同一支影片每次結果相同", "五字標要好認又不能亂編"),
            ("寫回 Excel", "openpyxl", "只動四台五字標欄，格式與公式不變", "沿用既有「12監看」表")]
    table(s, 0.67, 4.15, [1.45, 2.75, 4.5, 3.3], [0.34] + [0.4] * 6, rows, size=11)
    return s


def s_logic(prs):
    s = blank(prs)
    frame(s, "3.2 解法思路・3.4 施作方式", "判斷規則：廣告、換題、五字標各怎麼判", 8, "參數為 11/06 實跑設定（前 20 分鐘調整，之後固定）。廣告照片：東森 12:34:46–12:38:44 同一段破口，平均截 4 張。")
    tag(s, 0, 0.6, 0, "換題與字幕（Q2）・廣告包框（Q3）")
    cols = [(0.67, 4.0), (4.82, 3.85), (8.82, 3.85)]
    heads = ["廣告：10 種訊號，任兩種成立", "換題：標題穩定 7 秒＋1 項佐證", "五字標：AI 寫、逐字檢查"]
    for (x, w), h in zip(cols, heads):
        box(s, x, 1.85, w, 5.1, CARD, BORDER, 0.08)
        label_box(s, x, 1.85, w, 0.42, h, SOFTRED, None, 13, True, RED, radius=0.08, pad=0.05)
    # ads
    x, w = cols[0]
    sig = ["主畫面版面改變", "標題列底色改變", "台徽消失", "黑畫面切換", "廣告用語的語音", "聲音指紋命中", "跑馬燈消失", "標題列出現廣告文案", "標題列輪播預告", "畫面時鐘消失"]
    for i, t in enumerate(sig):
        text(s, x + 0.15 + (i % 2) * 1.95, 2.38 + (i // 2) * 0.27, 1.9, 0.27, f"{i + 1}. {t}", 10, False, INK)
    text(s, x + 0.15, 3.8, w - 0.3, 1.2, ["持續 10 秒才進廣告；不到 25 秒不算破口", "8 秒內的空隙併成同一段", "回新聞：版面、底色、主播或語音恢復 5 秒", "包框期間凍結標題列，殘留舊標題不採用"], 10, False, INK, space=2)
    for i, f in enumerate(["ettv_1234_0_ad05.jpg", "ettv_1234_0_ad35.jpg", "ettv_1234_0_ad65.jpg", "ettv_1234_0_ad95.jpg"]):
        pic(s, f, x + 0.4 + (i % 2) * 1.65, 5.0 + (i // 2) * 0.95, w=1.55)
    # story change
    x, w = cols[1]
    text(s, x + 0.15, 2.38, w - 0.3, 0.28, "佐證（任一項）", 11, True, RED)
    text(s, x + 0.15, 2.68, w - 0.3, 1.35, ["前後 20 秒的語音話題不同", "前後 5 秒出現主播鏡頭", "說話的人換了", "25 秒內閃過記者名條"], 11, False, INK, space=2)
    text(s, x + 0.15, 4.05, w - 0.3, 0.28, "不算新題", 11, True, RED)
    text(s, x + 0.15, 4.35, w - 0.3, 1.5, ["3 分鐘內重複出現的標題，併回原本那則", "廣告前不到 20 秒的片段，併進廣告", "只閃 5 秒以內的名條，不當標題", "2 分鐘內輪播 6 個以上標題，視為預告"], 11, False, INK, space=2)
    box(s, x + 0.15, 5.95, w - 0.3, 0.85, SOFT, None, 0.06)
    text(s, x + 0.28, 5.95, w - 0.55, 0.85, "四台各自獨立判斷；一台出狀況，不影響其他三台。", 11, True, INK, anchor=MSO_ANCHOR.MIDDLE)
    # labels
    x, w = cols[2]
    flow = [("來源", "這則的標題列文字＋語音"), ("AI 寫標", "4–6 個字"), ("逐字檢查", "長度 4–6；每個人名、地名都在來源裡；社會新聞要有地點"), ("沒過", "重寫一次；再沒過就從標題直接摘字")]
    y = 2.4
    for i, (a, b) in enumerate(flow):
        hh = 0.78 if i == 2 else 0.55
        box(s, x + 0.15, y, w - 0.3, hh, SOFTOR if i == 3 else CARD, BORDER, 0.06)
        text(s, x + 0.27, y, w - 0.5, hh, [[(a + "　", {"bold": True, "color": RED}), (b, {})]], 11, False, INK, anchor=MSO_ANCHOR.MIDDLE)
        y += hh + 0.12
        if i < 3:
            arrow(s, x + w / 2, y - 0.12, x + w / 2, y - 0.01, RED, 1.25)
    text(s, x + 0.15, 5.25, w - 0.3, 0.28, "例：TVBS-N 12:04", 11, True, RED)
    text(s, x + 0.15, 5.55, w - 0.3, 1.3, [[("標題　", {"color": MUTE}), ("男二度毒駕拒檢…下秒遭車撞飛", {})], [("五字標　", {"color": MUTE}), ("毒駕遭車撞飛", {"bold": True, "size": 14})],
                                          [("每個字都出自原標題，不會亂編。", {"color": MUTE, "size": 10})]], 11, False, INK, space=3)
    return s


def s_results(prs):
    s = blank(prs)
    frame(s, "2.2 評估指標・2.3 驗證方式・4.1 評估結果・4.2 結論", "結果：準確度超過目標，90 格全自動", 9, "對照答案：TVBS-N 午間一小時人工標註，逐分鐘比對。前 20 分鐘調整規則，12:20 之後只測、不再修改。")
    tag(s, 0, 0.6, 0, "Excel 與驗證（Q4）・進度（Q5）")
    rows = [("指標", "怎麼算", "目標", "權重", "實測"),
            ("換題準確度", "和人工答案比，起始分鐘差 1 分鐘內算對", "0.85", "30%", "0.885"),
            ("廣告準確度", "同上，以分鐘計", "0.90", "25%", "0.952"),
            ("自動填寫率", "90 格中由系統自動填的比例", "100%", "20%", "100%"),
            ("處理時間", "四台一小時影片處理完", "15 分", "25%", "Mac 單機 46 分；顯示卡並行版已備")]
    table(s, 0.67, 1.9, [1.4, 3.0, 0.7, 0.65, 2.0], [0.34, 0.42, 0.42, 0.42, 0.5], rows, size=11)
    cd = CategoryChartData()
    cd.categories = ["換題準確度", "廣告準確度"]
    cd.add_series("實測", (0.885, 0.952))
    cd.add_series("目標", (0.85, 0.90))
    gf = s.shapes.add_chart(XL_CHART_TYPE.BAR_CLUSTERED, Inches(8.65), Inches(2.15), Inches(4.0), Inches(1.95), cd)
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
    text(s, 8.65, 1.85, 4.0, 0.28, "準確度：實測對目標", 12, True, RED)
    text(s, 0.67, 4.25, 5, 0.28, "產出的「12監看」（紅框＝換題那分鐘）", 12, True, RED)
    pic(s, "excel_annot.png", 0.67, 4.57, w=4.6)
    x = 5.5
    text(s, x, 4.25, 3.6, 0.28, "數得出來的成果", 12, True, RED)
    for i, (a, b) in enumerate([("23／27", "換題點找到"), ("10／10", "廣告分鐘全找到"), ("46 分", "一台 Mac 處理完四台一小時")]):
        box(s, x, 4.6 + i * 0.75, 3.45, 0.65, CARD, BORDER, 0.06)
        text(s, x + 0.15, 4.6 + i * 0.75, 1.6, 0.65, a, 17, True, RED, anchor=MSO_ANCHOR.MIDDLE)
        text(s, x + 1.75, 4.6 + i * 0.75, 1.65, 0.65, b, 10, False, INK, anchor=MSO_ANCHOR.MIDDLE)
    x = 9.2
    text(s, x, 4.25, 3.5, 0.28, "結論與取捨（4.2）", 12, True, RED)
    text(s, x, 4.57, 3.45, 2.4, ["採用完整方案：文字、語音、畫面一起看；拿掉語音時換題準確度只有 0.758，加上語音升到 0.885。",
                                 "時間幾乎都花在讀畫面與語音，這段四台可同時跑；換顯示卡機器直接縮短，判斷本身只要 2.7 分。",
                                 "不追求零人工：系統知道自己哪裡沒把握，主動排給人看。"], 10.5, False, INK, space=4)
    return s


def s_plan(prs):
    s = blank(prs)
    frame(s, "5.1 前 90 天・5.2 風險・5.3 人與關係", "推動：90 天三階段，達標才擴大", 10, "門檻與時程為提案設定，待業師確認。關注點參考：路透研究所英國記者調查（60% 擔心損害公眾信任）。")
    tag(s, 0, 0.6, 0, "進度與擴大（Q5）")
    rows = [("階段", "目標", "交付物", "驗收 KPI"),
            ("Day 0–30", "四台答案、統一記法", "四台各一小時雙人標註", "兩位標註者一致性 ≥ 0.8"),
            ("Day 31–60", "午間和人工並行", "並行報告、顯示卡機實測", "準確度 ≥ 0.80、處理 ≤ 15 分"),
            ("Day 61–90", "全天四台交付", "每日品質日報、規則版本", "準確度 ≥ 0.85、人工 ≤ 3 小時／日")]
    table(s, 0.67, 1.85, [1.2, 1.85, 2.3, 2.55], 0.4, rows, size=11)
    box(s, 0.67, 3.53, 7.9, 0.4, SOFTRED, None, 0.08)
    text(s, 0.85, 3.53, 7.6, 0.4, "Go／No-go：達到該階段 KPI 才進下一階段；準確度低於 0.70 就停止擴大、回頭調整規則。", 11, True, RED, anchor=MSO_ANCHOR.MIDDLE)
    text(s, 0.67, 4.08, 7.9, 0.28, "上線後怎麼跑（已完成：全天自動處理、品質日報、人工複查與修正版）", 12, True, RED)
    flow = [("三立側錄系統", "既有，不取代"), ("每小時自動切檔", "排入待處理清單"), ("地端伺服器", "四台同時處理"), ("監看表＋品質日報", "交監看與行政")]
    for i, (a, b) in enumerate(flow):
        xx = 0.67 + i * 2.0
        label_box(s, xx, 4.4, 1.85, 0.7, [[(a, {"bold": True, "size": 11})], [(b, {"size": 10, "color": "FFFFFF" if i == 3 else MUTE})]], RED if i == 3 else CARD, None if i == 3 else BORDER, 11, False, "FFFFFF" if i == 3 else INK, pad=0.04)
        if i < 3:
            arrow(s, xx + 1.86, 4.75, xx + 1.99, 4.75, RED, 1.25)
    text(s, 0.67, 5.25, 7.9, 0.28, "利害關係人與溝通（5.3）", 12, True, RED)
    rows2 = [("對象", "關注", "怎麼溝通"),
             ("監看員", "怕被取代、多一份工作", "先並行不取代，角色變成複查者"),
             ("新聞部主管", "AI 出錯誰負責", "人保留最終決定，每格有依據與修正紀錄"),
             ("AI 轉型辦公室、IT、行政", "維運、介接、表格記法", "只接檔案；不新增記法；規則可退版")]
    table(s, 0.67, 5.55, [2.3, 2.0, 3.6], [0.32, 0.36, 0.36, 0.36], rows2, size=10.5)
    text(s, 8.85, 1.85, 3.8, 0.28, "風險與已備對策（5.2）", 12, True, RED)
    gx, gy, cell = 9.55, 2.25, 1.0
    for r in range(3):  # row 0 = high impact
        for c in range(3):  # col 0 = low likelihood
            score = (2 - r) + c
            fill = SOFTRED if score >= 3 else (SOFT if score == 2 else "F6F6F6")
            box(s, gx + c * cell, gy + r * cell, cell - 0.04, cell - 0.04, fill, None, 0)
    for i, t in enumerate(["衝擊高", "衝擊中", "衝擊低"]):
        text(s, gx - 0.7, gy + i * cell + 0.36, 0.65, 0.3, t, 10, True, MUTE, PP_ALIGN.RIGHT)
    for i, t in enumerate(["機率低", "機率中", "機率高"]):
        text(s, gx + i * cell, gy + 3 * cell + 0.02, cell, 0.25, t, 10, True, MUTE, PP_ALIGN.CENTER)
    pts = [(1, 2, 1), (2, 1, 0), (3, 1, 0), (4, 0, 0), (5, 1, 2)]  # (id, likelihood col, impact row)
    offs = {}
    for pid, c, r in pts:
        n = offs.get((c, r), 0)
        offs[(c, r)] = n + 1
        label_box(s, gx + c * cell + 0.1 + n * 0.42, gy + r * cell + 0.3, 0.36, 0.36, str(pid), RED, None, 11, True, "FFFFFF", radius=0.18, pad=0)
    legend = ["1 擴到四台需要更多對照答案 → Day 0–30 雙人標註", "2 全天量變大 → 顯示卡機四台同時處理", "3 電視台改版、標題位置變 → 校正頁＋日報告警",
              "4 影片版權 → 影片不出內網，只留文字", "5 摘字的標題較生硬 → 排進複查"]
    text(s, 8.85, 5.6, 3.85, 1.3, legend, 9.5, False, INK, space=2)
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
    for fn in (s_summary, s_current, s_cause, s_gap, s_future, s_solution, s_logic, s_results, s_plan):
        fn(prs)
    prs.save(out)
    print("slides:", len(prs.slides))


if __name__ == "__main__":
    main(*sys.argv[1:4])
