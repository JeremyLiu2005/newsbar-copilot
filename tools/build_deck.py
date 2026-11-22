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


def cover_key(s):
    """Which pages answer the five questions of the challenge (on the dark cover, right edge)."""
    text(s, 10.5, 1.55, 2.5, 0.3, "五個問題在哪一頁", 13, True, "F97316")
    rows = [("影片到 Excel 全自動（Q1）", "第 7、8 頁"), ("換題、字幕與對時（Q2）", "第 4、8 頁"), ("廣告包框（Q3）", "第 8 頁"),
            ("Excel 與驗證（Q4）", "第 7、8、9 頁"), ("進度與擴大（Q5）", "第 9、10 頁")]
    y = 2.0
    for a, b in rows:
        text(s, 10.5, y, 2.6, 0.25, a, 12, True, "FFFFFF")
        text(s, 10.5, y + 0.27, 2.5, 0.25, b, 11, False, "FCA5A5")
        y += 0.72


def s_summary(prs):
    s = blank(prs)
    frame(s, "摘要", "一個人盯不過四台：系統全填，人只複查", 2, "數字來自 11/06 12:00–13:00 四台實測；價值為假設值（每日 18 小時監看、時薪 NT$235、維運 22 萬），待業師確認。")
    rows = [
        ("問題痛點與價值", "一個人要同時盯四台，14 分鐘內至少 3 台同時換題，廣告包框又留著舊標題，人眼和反應速度跟不上，造成漏記與時間錯位。改善後每年約可釋出 5,585 小時。",
         "NT$109 萬", "每年淨效益（假設）"),
        ("核心解決方案", "推動 NewsBar Copilot：先判廣告、再判換題、最後寫五字標，自動填回既有的「12監看」表；人只複查系統沒把握的格子。由三立新聞部主導，AI 轉型辦公室協助導入。",
         "90 格", "一小時四台全自動填完"),
        ("實測結果", "11/06 四台一小時實測：換題準確度 0.885（目標 0.85，達標）、廣告準確度 0.952（目標 0.90，達標）、自動填寫率 100%（達標）；處理時間 46 分（目標 15 分，未達標，換顯示卡機器實測中）。",
         "3／4", "關鍵指標達標"),
    ]
    y = 1.95
    for i, (a, b, v, c) in enumerate(rows):
        box(s, 0.67, y, 12.0, 1.55, CARD, BORDER, 0.08)
        label_box(s, 0.87, y + 0.5, 2.0, 0.55, a, SOFTRED, None, 14, True, RED, radius=0.1, pad=0.05)
        text(s, 3.1, y + 0.15, 6.6, 1.25, b, 13, False, INK, anchor=MSO_ANCHOR.MIDDLE, line=1.15)
        text(s, 9.9, y + 0.2, 2.6, 0.7, v, 26, True, ORANGE if i == 1 else RED, PP_ALIGN.CENTER, MSO_ANCHOR.MIDDLE)
        text(s, 9.9, y + 0.9, 2.6, 0.4, c, 11, False, MUTE, PP_ALIGN.CENTER)
        y += 1.7
    return s


def s_current(prs):
    s = blank(prs)
    frame(s, "1.1 現況分析", "現況：人工逐分鐘判斷、填表", 3, "角色與流程整理自課程題目說明與 10/26 歷史監看範例。各節點工時沒有公開資料，整體以每日 18 小時監看假設，待業師確認。")
    steps = ["看四台畫面", "判斷換題或廣告", "寫五字標", "填入分鐘格", "隔日貼收視率"]
    x0, cw = 2.15, 2.1
    for i, n in enumerate(steps):
        box(s, x0 + i * cw, 1.9, cw - 0.05, 0.5, RED if i == 1 else SOFTRED, None, 0, shape=MSO_SHAPE.PENTAGON)
        text(s, x0 + i * cw + 0.12, 1.9, cw - 0.3, 0.5, n, 13, True, "FFFFFF" if i == 1 else RED, PP_ALIGN.LEFT, MSO_ANCHOR.MIDDLE)
    rows = [
        ("負責人", "當班監看人員", "當班監看人員", "當班監看人員", "當班監看人員", "行政人員"),
        ("工具", "四台側錄影片", "眼睛與經驗", "手打", "Excel「12監看」", "Excel"),
        ("工時", "每日約 18 小時（假設）", "含在監看中", "含在監看中", "含在監看中", "未量測"),
        ("痛點", "一人同時盯四台", "14 分鐘內至少 3 台同時換題", "要讓人認得是哪則新聞", "漏記、時間對不上", "表格錯一分鐘，收視高點算給錯的新聞"),
    ]
    table(s, 0.67, 2.5, [1.48] + [cw] * 5, 0.62, rows, size=11, head=False)
    box(s, x0 + cw - 0.02, 2.48, cw + 0.0, 2.52, None, RED, 0, lw=2.5)
    text(s, x0 + cw, 5.05, cw, 0.3, "關鍵瓶頸", 12, True, RED, PP_ALIGN.CENTER)
    box(s, 0.67, 5.45, 12.0, 1.2, SOFT, None, 0.08)
    text(s, 0.9, 5.55, 11.6, 1.05, [
        [("10/26 歷史範例：", {"bold": True, "color": RED}), ("一小時共 101 筆標記；14 分鐘內至少 3 台同時換題（課程提供）。", {})],
        [("填表規則：", {"bold": True, "color": RED}), ("換題那一分鐘填標、延續留白、同分鐘多則用「/」、廣告起始那一分鐘填「廣告」。", {})],
        [("用途：", {"bold": True, "color": RED}), ("隔日貼上收視率，當作收視與廣告破口分析的事件索引。", {})]], 13, False, INK, space=5)
    return s


def s_cause(prs):
    s = blank(prs)
    frame(s, "1.2 真因分析・1.3 路由判斷", "真因：要同時盯多路訊號，人眼做不到", 4, "魚骨圖整理自題目說明與 10/26 範例；路由判斷先問「能不能不用 AI 解決」。")
    tag(s, 0, 0.6, 0, "換題與字幕（Q2）")
    cats = [("人", ["一人同時盯四台", "多台同時換題來不及反應"]), ("畫面", ["包框殘留舊標題", "跑馬燈、快訊、名條干擾"]),
            ("規則", ["同一標題重複出現", "廣告後接回同一則怎麼記沒統一"]), ("流程", ["逐分鐘手填", "隔日貼收視率才發現錯"])]
    pos = [(0.67, 1.9), (3.8, 1.9), (0.67, 3.55), (3.8, 3.55)]
    for (cat, items), (x, y) in zip(cats, pos):
        box(s, x, y, 2.95, 1.25, CARD, BORDER, 0.08)
        text(s, x + 0.15, y + 0.07, 2.7, 0.3, cat, 13, True, RED)
        text(s, x + 0.15, y + 0.42, 2.7, 0.8, items, 11, False, INK, space=2)
    box(s, 0.67, 3.27, 6.1, 0.05, RED, None, 0)
    arrow(s, 6.77, 3.3, 7.05, 3.3, RED, 2.0)
    label_box(s, 7.08, 2.85, 1.5, 0.9, ["漏記、", "時間錯位"], RED, RED, 13, True, "FFFFFF")
    box(s, 8.85, 1.9, 3.8, 2.9, SOFTRED, None, 0.08)
    text(s, 9.05, 2.0, 3.45, 2.7, [[("表面現象", {"bold": True, "color": RED})], "漏記、時間錯位", [("實際根因", {"bold": True, "color": RED})],
                                    "換題與廣告的判斷，要同時盯畫面文字、語音、版面好幾路訊號，四台一起來時人眼與反應速度做不到。"], 14, False, INK, anchor=MSO_ANCHOR.MIDDLE, space=6)
    text(s, 0.67, 4.98, 6, 0.28, "路由判斷：每個真因用什麼處理", 13, True, RED)
    rows = [("真因", "處理方式", "理由"),
            ("多台同時換題，一人看不完", "AI 導入", "加人只能分攤，判斷仍靠反應速度；機器可四台同時看"),
            ("包框殘字、跑馬燈與字幕干擾", "AI 導入＋規則", "要同時比對多種畫面訊號，規則寫得清楚，交給機器最穩"),
            ("記法沒統一（廣告後接回、節目預告）", "管理制度調整", "不需要 AI，請業師訂出統一記法即可"),
            ("擔心 AI 出錯或取代人", "團隊溝通", "人保留最終決定權，系統只排出要看的格子")]
    table(s, 0.67, 5.3, [3.5, 1.9, 6.6], [0.3, 0.33, 0.33, 0.33, 0.33], rows, size=11)
    return s


def s_gap(prs):
    s = blank(prs)
    frame(s, "1.4 改善空間與商業價值", "改善空間：每年約 5,585 小時，淨效益約 NT$109 萬", 5, "價值為假設，待業師確認：每日 18 小時 × 365 天 × 系統接手 85% × 時薪 NT$235。時薪參考：2026 年基本時薪 196 元、2027 年預定 205 元（勞動部）。")
    rows = [("指標", "現況基準", "目標", "落差"),
            ("人工監看工時（每日）", "18 小時（假設）", "約 2.7 小時", "減少 15.3 小時"),
            ("換題準確度", "人工未量測", "0.85", "實測 0.885，已達標"),
            ("廣告準確度", "人工未量測", "0.90", "實測 0.952，已達標"),
            ("四台一小時處理時間", "人工即時看", "15 分鐘內", "實測 46 分，差 31 分")]
    table(s, 0.67, 1.95, [2.4, 1.75, 1.35, 2.0], 0.5, rows, size=11)
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
    text(s, 0.67, 4.65, 8, 0.28, "維持現狀一年的風險", 13, True, RED)
    risks = [("下游分析失準", "表格漏記或錯一分鐘，隔日收視與廣告破口分析會把收視高點算給錯的新聞。", "衝擊：高"),
             ("人力持續被佔用", "每年約 131 萬元的人力價值花在逐分鐘填表，台數或時段增加時成本跟著增加。", "衝擊：中"),
             ("尖峰品質不穩", "多台同時換題時只能靠反應速度，疲勞時更容易漏記。", "衝擊：中")]
    for i, (a, b, c) in enumerate(risks):
        x = 0.67 + i * 4.06
        box(s, x, 5.0, 3.86, 1.5, CARD, BORDER, 0.08)
        text(s, x + 0.18, 5.08, 2.4, 0.3, a, 13, True, INK)
        label_box(s, x + 2.65, 5.1, 1.05, 0.28, c, SOFTRED, None, 10, True, RED, radius=0.14, pad=0.02)
        text(s, x + 0.18, 5.48, 3.5, 1.0, b, 13, False, INK)
    return s


def s_future(prs):
    s = blank(prs)
    frame(s, "2.1 未來的工作方式", "未來：系統全填，人只看沒把握的格子", 6, "負責人與審查週期為提案設定，待業師確認。工時以每日 18 小時、系統接手 85% 估算。截圖為操作原型（11/06 實測結果，複查紀錄為示範）。")
    sc = [("A 維持人工", "加監看人力、統一記法", "管理調整", CARD, BORDER), ("B 不用語音", "只看標題文字與畫面訊號", "AI＋規則", CARD, BORDER),
          ("C NewsBar 完整方案", "文字、語音、畫面一起看，AI 寫標", "AI＋規則＋人複查（採用）", SOFTRED, RED)]
    for i, (a, b, c, f, l) in enumerate(sc):
        x = 0.67 + i * 2.5
        box(s, x, 1.9, 2.38, 1.25, f, l, 0.08, lw=1.5 if i == 2 else 1.0)
        text(s, x + 0.12, 1.96, 2.2, 0.3, a, 12, True, RED if i == 2 else INK)
        text(s, x + 0.12, 2.3, 2.2, 0.45, b, 11, False, INK)
        text(s, x + 0.12, 2.78, 2.2, 0.3, c, 10, True, MUTE)
    text(s, 0.67, 3.3, 7.4, 0.28, "人機分工與交接", 13, True, RED)
    flow = [("系統", "90 格全填，每格留下依據"), ("交接條件", "系統沒把握的格子，自動進抽查清單"), ("監看員", "看畫面與依據：對、改寫、不該填"), ("結果", "改正另存修正版，原表不動")]
    for i, (a, b) in enumerate(flow):
        x = 0.67 + i * 1.88
        label_box(s, x, 3.62, 1.75, 0.95, [[(a, {"bold": True, "size": 12, "color": RED})], [(b, {"size": 10})]], CARD, BORDER, pad=0.06)
        if i < 3:
            arrow(s, x + 1.76, 4.1, x + 1.87, 4.1, RED, 1.25)
    text(s, 0.67, 4.72, 3.6, 0.28, "管理監督", 13, True, RED)
    text(s, 0.67, 5.02, 3.6, 1.9, ["負責人：新聞部指定窗口", "每天看品質日報（填寫率、低信心比例）", "每週依修正紀錄更新規則", "規則連兩週沒達標，自動退回上一版"], 11, False, INK, space=3)
    text(s, 4.45, 4.72, 3.6, 0.28, "人員角色的改變", 13, True, RED)
    text(s, 4.45, 5.02, 3.6, 1.9, ["監看員：從逐分鐘填表，變成只複查沒把握的格子", "需要的新能力：看判斷依據、用複查台", "人工工時：每日約 18 小時降到約 2.7 小時"], 11, False, INK, space=3)
    pic(s, "rv_card.png", 8.3, 1.9, h=3.55)
    text(s, 8.3, 5.5, 2.9, 0.5, "複查台：每格附畫面與依據，一鍵判定。", 10, False, MUTE)
    for i, (f, t) in enumerate([("qr_proto.png", "流程示範"), ("qr_monitor.png", "結果網頁"), ("qr_github.png", "GitHub 可下載")]):
        qy = 1.9 + i * 1.65
        s.shapes.add_picture(str(A / f), Inches(11.35), Inches(qy), width=Inches(1.2))
        text(s, 11.2, qy + 1.22, 1.5, 0.25, t, 10, False, INK, PP_ALIGN.CENTER)
    return s


def s_solution(prs):
    s = blank(prs)
    frame(s, "3.1 解決方案・3.3 採用工具", "解法：NewsBar Copilot 把四台側錄自動轉成「12監看」", 7, "全程在本機執行，影片與表格不離開三立。Mac 版已實測；NVIDIA 顯示卡版程式已備，尚未實測（GitHub 兩種版本皆可下載）。")
    tag(s, 0, 0.6, 0, "全自動流程（Q1）・Excel（Q4）")
    box(s, 0.67, 1.9, 5.9, 1.75, CARD, BORDER, 0.08)
    text(s, 0.87, 1.98, 5.5, 1.65, [[("輸入", {"bold": True, "color": RED})], "四台側錄影片（.ts／.mp4）＋乾淨版「12監看」範本（.xlsx）",
                                    [("輸出", {"bold": True, "color": RED})], "填好的「12監看」：每分鐘一格，換題才填五字標；另附每格判斷依據與抽查清單"], 12, False, INK, space=3)
    box(s, 6.77, 1.9, 5.9, 1.75, SOFT, None, 0.08)
    text(s, 6.97, 1.98, 5.5, 1.65, [[("不做的事（Non-goals）", {"bold": True, "color": RED})], "不填收視率欄、不填三立自家欄", "不新增欄位、不做秒級紀錄",
                                    "不判斷新聞真偽或立場", "不取代三立既有側錄系統，只接在它後面"], 12, False, INK, space=3)
    text(s, 0.67, 3.85, 8, 0.28, "系統架構", 13, True, RED)
    arch = ["側錄影片", "抽畫面與音軌、畫面時鐘校時", "文字辨識｜語音轉錄｜畫面訊號｜聲音指紋", "廣告判定", "換題判斷", "AI 寫標＋逐字檢查", "寫回 Excel"]
    ws = [1.05, 1.75, 2.75, 1.05, 1.05, 1.55, 1.2]
    x = 0.67
    for i, (t, w) in enumerate(zip(arch, ws)):
        label_box(s, x, 4.17, w, 0.72, t, RED if i == 6 else CARD, RED if i == 6 else BORDER, 11, True, "FFFFFF" if i == 6 else INK, pad=0.05)
        if i < 6:
            arrow(s, x + w + 0.01, 4.53, x + w + 0.18, 4.53, RED, 1.25)
        x += w + 0.19
    rows = [("功能", "選用", "為什麼選", "已知限制與對策"),
            ("文字辨識", "Apple Vision／PaddleOCR", "本機執行、支援繁中", "小字或背景複雜會讀錯 → 每 5 秒多數決"),
            ("語音轉錄", "faster-whisper", "本機執行、速度快", "一次只能轉一台，是速度瓶頸 → 換顯示卡機器並行"),
            ("AI 寫標", "qwen2.5:7b（本機）", "影片與文字不出內網", "會寫超過 6 字或用標題外的字 → 逐字檢查，不過改規則摘字")]
    table(s, 0.67, 5.1, [1.4, 2.6, 2.4, 5.6], [0.36, 0.5, 0.5, 0.5], rows, size=12)
    return s


def s_logic(prs):
    s = blank(prs)
    frame(s, "3.2 解法思路・3.4 施作方式", "做法：每個問題用一個技術解決", 8, "廣告破口照片：東森 12:34:46–12:38:44，頭尾平均截 4 張。Excel 為 11/06 實際產出節錄（12:05 到 12:34 之間省略）。")
    tag(s, 0, 0.6, 0, "流程（Q1）・換題與字幕（Q2）・廣告包框（Q3）・Excel（Q4）")
    text(s, 0.85, 1.85, 3.0, 0.28, "要解決的問題", 12, True, RED)
    text(s, 3.85, 1.85, 3.0, 0.28, "用的技術", 12, True, RED)
    rows = [
        ("一人盯不過四台", "四台各自平行處理，一台失敗不影響其他台"),
        ("廣告包框殘留舊標題", "10 種訊號任兩種成立才算廣告；包框期間凍結標題列文字；聲音指紋認出已知廣告"),
        ("跑馬燈、快訊、名條干擾", "只讀固定的標題列區域；每 5 秒多數決，避免單次讀錯"),
        ("同一標題重複出現、短暫字幕", "標題穩定 7 秒才算新題；3 分鐘內重複出現，併回原本那一則"),
        ("五字標不能亂編", "AI 寫標，每個字都要出現在標題裡；兩次不過就改用規則摘字"),
        ("填在對的台、對的分鐘", "讀畫面時鐘校時，秒轉成分鐘格；只寫四台五字標欄"),
        ("沒把握的格子", "照常填寫並標低信心，進抽查清單由人確認"),
    ]
    y = 2.18
    for a, b in rows:
        box(s, 0.67, y, 6.95, 0.6, CARD, BORDER, 0.06)
        text(s, 0.82, y, 2.85, 0.6, a, 12, True, INK, anchor=MSO_ANCHOR.MIDDLE)
        arrow(s, 3.58, y + 0.3, 3.78, y + 0.3, RED, 1.25)
        text(s, 3.85, y, 3.7, 0.6, b, 10.5, False, INK, anchor=MSO_ANCHOR.MIDDLE)
        y += 0.67
    text(s, 7.9, 1.85, 4.8, 0.28, "廣告判定流程", 12, True, RED)
    st = ["新聞", "疑似廣告", "廣告", "疑似回新聞", "新聞"]
    bw = 0.86
    for i, n in enumerate(st):
        x = 7.9 + i * (bw + 0.14)
        label_box(s, x, 2.18, bw, 0.42, n, SOFT if n == "廣告" else CARD, RED if n == "廣告" else BORDER, 10, True, INK, pad=0.02)
        if i < 4:
            arrow(s, x + bw + 0.005, 2.39, x + bw + 0.135, 2.39, RED, 1.0)
    text(s, 7.9, 2.66, 4.8, 0.45, "兩種以上訊號 → 持續 25 秒 → 版面恢復、語音、出現新標題三項成立才回新聞", 10, False, MUTE)
    frames = ["ettv_1234_0_ad05.jpg", "ettv_1234_0_ad35.jpg", "ettv_1234_0_ad65.jpg", "ettv_1234_0_ad95.jpg"]
    for i, f in enumerate(frames):
        pic(s, f, 7.9 + i * 1.2, 3.15, w=1.13)
        text(s, 7.9 + i * 1.2, 3.8, 1.13, 0.2, ["12:34:57", "12:36:09", "12:37:20", "12:38:32"][i], 9, True, INK)
    text(s, 7.9, 4.1, 4.8, 0.28, "輸出：填好的「12監看」（紅框＝換題那分鐘）", 12, True, RED)
    pic(s, "excel_annot.png", 7.9, 4.42, w=4.75)
    text(s, 7.9, 6.75, 4.8, 0.3, "例：「男二度毒駕拒檢…下秒遭車撞飛」→「毒駕遭車撞飛」，填入 TVBS-N 12:04", 9, False, INK)
    return s


def s_results(prs):
    s = blank(prs)
    frame(s, "2.2 評估指標・2.3 驗證方式・4.1 評估結果・4.2 結論", "結果：準確度與自動化達標，處理時間未達", 9, "對照答案：TVBS 一台，由研究者先標好（約 30 個點，尚非正式雙人標註）。加權達成度＝各指標實測÷目標（最高 100%）× 權重。")
    tag(s, 0, 0.6, 0, "Excel 與驗證（Q4）・進度與限制（Q5）")
    rows = [("指標", "怎麼算", "目標", "權重", "實測", "結果"),
            ("換題準確度", "和人工答案比，起始分鐘差 1 分鐘內算對", "0.85", "30%", "0.885", "達標"),
            ("廣告準確度", "同上，以分鐘計", "0.90", "25%", "0.952", "達標"),
            ("自動填寫率", "90 格中由系統自動填的比例", "100%", "20%", "100%", "達標"),
            ("處理時間", "四台一小時影片處理完", "15 分內", "25%", "46 分", "未達標")]
    table(s, 0.67, 1.95, [1.55, 3.25, 0.85, 0.75, 0.8, 0.85], 0.45, rows, size=11, hl_rows=(4,))
    box(s, 0.67, 4.32, 8.05, 0.7, SOFT, None, 0.08)
    text(s, 0.87, 4.32, 7.7, 0.7, [[("加權達成度：", {"bold": True, "color": RED}), ("83／100", {"bold": True, "color": RED, "size": 18}),
                                    ("　三項達標，處理時間拖低分數", {})]], 13, False, INK, anchor=MSO_ANCHOR.MIDDLE)
    text(s, 9.0, 1.9, 3.7, 0.28, "怎麼測（2.3）", 13, True, RED)
    text(s, 9.0, 2.25, 3.65, 2.7, ["測試資料：11/06 12:00–13:00 四台側錄（課程提供）", "步驟：前 20 分鐘調整規則，12:20 之後只測、不再改",
                                   "對照組：拿掉語音的版本（情境 B），換題準確度只有 0.758", "關鍵假設：加上語音會更準（已證實）；四台一小時能 15 分鐘內處理完（未證實，風險高）"], 11, False, INK, space=5)
    text(s, 0.67, 5.2, 8, 0.28, "結論與取捨（4.2）", 13, True, RED)
    text(s, 0.67, 5.52, 12.0, 1.45, [
        [("採用情境 C：", {"bold": True}), ("準確度與自動化都達標，人只需複查沒把握的格子。", {})],
        [("取捨：", {"bold": True}), ("用處理時間換準確度（語音轉錄最慢，但拿掉語音準確度掉到 0.758）；不追求零人工，沒把握的格子保留給人。", {})],
        [("未達標的下一步：", {"bold": True}), ("處理時間 46 分，花在讀標題與語音轉錄（43.5 分），判斷本身只要 2.7 分；換顯示卡機器並行，目標 15 分內。", {})]], 12, False, INK, space=4)
    return s


def s_plan(prs):
    s = blank(prs)
    frame(s, "5.1 前 90 天・5.2 風險・5.3 人與關係", "推動：90 天三階段，達標才擴大", 10, "門檻與時程為提案設定，待業師確認。關注點參考：路透研究所英國記者調查（62% 視 AI 為大威脅、60% 擔心損害公眾信任）；台灣的同類調查未找到。")
    tag(s, 0, 0.6, 0, "進度與擴大（Q5）")
    rows = [("階段", "目標", "交付物", "驗收 KPI"),
            ("Day 0–30", "補齊答案、統一記法", "四台各一小時正式標註", "兩位標註者一致性 ≥ 0.8"),
            ("Day 31–60", "午間和人工並行比較", "並行比較報告、顯示卡機器實測", "準確度 ≥ 0.80、處理 ≤ 15 分"),
            ("Day 61–90", "全天四台交付", "每日品質日報、規則版本紀錄", "準確度 ≥ 0.85、人工 ≤ 3 小時／日")]
    table(s, 0.67, 1.9, [1.25, 1.95, 2.35, 2.35], 0.45, rows, size=11)
    box(s, 0.67, 3.78, 7.9, 0.45, SOFTRED, None, 0.08)
    text(s, 0.85, 3.78, 7.6, 0.45, "Go／No-go：達到該階段 KPI 才進下一階段；準確度低於 0.70 就停止擴大，回頭調整規則。", 11, True, RED, anchor=MSO_ANCHOR.MIDDLE)
    text(s, 0.67, 4.38, 7.9, 0.28, "利害關係人與溝通（5.3）", 13, True, RED)
    rows2 = [("對象", "關注", "溝通方式"),
             ("監看員", "怕被取代、多一份工作", "先並行不取代，角色變成複查者"),
             ("新聞部主管", "AI 出錯，誰負責", "人保留最終決定，每格有依據與修正紀錄"),
             ("行政（貼收視率）", "表格多一種記法", "不新增記法，沒把握的格子照常填"),
             ("AI 轉型辦公室、IT", "維運與系統介接", "只接檔案，不改既有側錄；規則可退版")]
    table(s, 0.67, 4.7, [1.75, 2.45, 3.7], 0.44, rows2, size=11)
    text(s, 8.85, 1.85, 3.8, 0.28, "風險矩陣（5.2）", 13, True, RED)
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
    legend = ["1 對照答案不足（機率高、衝擊中）→ Day 0–30 補標", "2 處理時間不達標（中、高）→ 顯示卡機器實測", "3 電視台改版，標題位置變（中、高）→ 校正頁、日報告警",
              "4 版權與資料使用（低、高）→ 影片不出內網，只留文字", "5 規則摘字的標題不通順（中、低）→ 列入抽查"]
    text(s, 8.85, 5.75, 3.85, 1.25, legend, 9, False, INK, space=1)
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
