"""Build the S36 submission deck on top of the base .pptx (keeps the cover, draws nine pages: summary plus the UNEXT proposal sections, answering the five official questions).

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
    rows = [("Q1 影片如何自動變成 Excel", "第 6 頁"), ("Q2 換題、字幕與五字標對時", "第 7 頁"), ("Q3 廣告包框", "第 8 頁"),
            ("Q4 Excel 呈現與驗證", "第 9 頁"), ("Q5 進度與擴大到全天", "第 10 頁")]
    y = 2.0
    for a, b in rows:
        text(s, 10.5, y, 2.7, 0.25, a, 12, True, "FFFFFF")
        text(s, 10.5, y + 0.27, 2.5, 0.25, b, 11, False, "FCA5A5")
        y += 0.72
    for i, (f, t) in enumerate([("qr_proto.png", "操作原型"), ("qr_monitor.png", "結果網頁"), ("qr_github.png", "GitHub")]):
        x = 9.55 + i * 1.12
        s.shapes.add_picture(str(A / f), Inches(x), Inches(5.75), width=Inches(0.95))
        text(s, x - 0.1, 6.73, 1.15, 0.25, t, 10, False, "FFFFFF", PP_ALIGN.CENTER)


def qtag(s, label):
    tag(s, 0, 0.6, 0, label)


def heat(s, gx, gy, cw2, ch):
    rows = heat_rows()
    cols = {"c": RED, "a": ORANGE, "": TRACK}
    for r, (k, name) in enumerate(CH):
        text(s, gx - 0.85, gy + r * ch, 0.8, ch, name, 9, True, INK, anchor=MSO_ANCHOR.MIDDLE)
        for m, v in enumerate(rows[k]):
            box(s, gx + m * cw2, gy + r * ch + 0.015, cw2 - 0.025, ch - 0.03, cols[v], None, 0)
    for m in range(0, 60, 15):
        text(s, gx + m * cw2, gy + 4 * ch + 0.03, 0.6, 0.2, f"12:{m:02d}", 8, False, MUTE)


def station(s, x, y, w, h, n, title, hot=False):
    box(s, x, y, w, h, RED if hot else CARD, None if hot else INK, 0, lw=1.0)
    label_box(s, x + 0.08, y + 0.08, 0.26, 0.26, str(n), "FFFFFF" if hot else INK, None, 10, True, RED if hot else "FFFFFF", radius=0, pad=0)
    text(s, x + 0.12, y + 0.36, w - 0.2, h - 0.4, title, 13, True, "FFFFFF" if hot else INK, anchor=MSO_ANCHOR.MIDDLE)


def chip(s, x, y, w, t, fill=SOFT):
    label_box(s, x, y, w, 0.32, t, fill, None, 10.5, False, INK, radius=0.04, pad=0.04)


def big_card(s, x, y, w, h, head, num, sub, fill=CARD):
    box(s, x, y, w, h, fill, BORDER, 0.08)
    text(s, x + 0.2, y + 0.12, w - 0.4, 0.3, head, 12, True, RED)
    text(s, x + 0.2, y + 0.45, w - 0.4, 0.7, num, 28, True, INK)
    text(s, x + 0.2, y + 1.3, w - 0.4, h - 1.4, sub, 15, False, INK, space=8)


def s_sum(prs):
    s = blank(prs)
    frame(s, "摘要", "一鍵填好四台「12監看」，人只看標黃的格子", 2, "價值依每日監看 4–18 小時估算，待業師確認。實測：11/06 12:00–13:00 四台。")
    cards = [("問題痛點與價值", "7–109 萬／年", ["真因：要同時比對多種訊號，一人盯四台跟不上", "結果：漏記、錯位，收視檢討算錯新聞"]),
             ("核心解決方案", "一鍵、90 格", ["把四台側錄填回原「12監看」", "新聞部主導，創造智能協助"]),
             ("驗證結果評估", "全部達標", [])]
    for i, (h, n, sub) in enumerate(cards):
        big_card(s, 0.67 + i * 4.06, 1.95, 3.86, 4.9, h, n, sub, SOFTRED if i == 2 else CARD)
    bars = [("換題", 0.885, 0.85), ("廣告", 0.952, 0.90)]
    for j, (n, v, t) in enumerate(bars):
        y = 3.35 + j * 0.9
        x0, wmax = 8.99, 3.4
        text(s, x0, y, 1.0, 0.3, n, 11, True, INK)
        box(s, x0, y + 0.32, wmax, 0.26, "FFFFFF", None, 0)
        box(s, x0, y + 0.32, wmax * v, 0.26, RED, None, 0)
        box(s, x0 + wmax * t - 0.015, y + 0.24, 0.03, 0.42, INK, None, 0)
        text(s, x0 + 1.1, y, 2.3, 0.3, f"{v:.3f}（目標 {t:.2f}）", 10.5, False, INK)
    text(s, 8.99, 5.25, 3.4, 0.8, ["填寫率 100%", "一台 Mac 46 分鐘"], 11.5, True, INK, space=3)
    return s


def s_prob(prs):
    s = blank(prs)
    frame(s, "1.1 現況分析・1.2 真因分析", "一人逐秒盯四台；真因是要同時比對多種訊號", 3, "熱力格：11/06 12:00–12:59 系統輸出。流程整理自 11/22 說明會。")
    sts = [("側錄四台", "四台各一小時"), ("逐秒判斷", "一人盯四台"), ("寫五字標", "要讓人認得"), ("填分鐘格", "漏記、錯位"), ("隔日灌收視率", "錯一分鐘就錯")]
    w, g = 2.2, 0.25
    for i, (t, pr) in enumerate(sts):
        x = 0.67 + i * (w + g)
        station(s, x, 1.85, w, 0.72, i + 1, t, hot=i == 1)
        if i < 4:
            arrow(s, x + w + 0.02, 2.21, x + w + g - 0.02, 2.21, MUTE, 1.5)
        chip(s, x, 2.65, w, pr, SOFTRED if i == 1 else SOFT)
    text(s, 0.67, 3.2, 8, 0.25, "四台每分鐘：紅＝換題、橘＝廣告", 11, True, RED)
    heat(s, 1.5, 3.5, 0.12, 0.2)
    box(s, 9.05, 3.25, 3.62, 1.2, SOFTRED, None, 0.08)
    text(s, 9.2, 3.25, 3.4, 1.2, [[("25 分鐘", {"bold": True, "size": 22, "color": RED})], "兩台以上同一分鐘換題"], 11, False, INK, anchor=MSO_ANCHOR.MIDDLE)
    sy = 5.92
    box(s, 0.8, sy - 0.02, 8.9, 0.04, INK, None, 0)
    label_box(s, 9.75, sy - 0.45, 2.92, 0.9, [[("漏記、時間錯位", {"bold": True})], [("收視檢討算錯新聞", {"size": 10})]], RED, None, 12, False, "FFFFFF", radius=0.08, pad=0.06)
    bones = [("人", "一人盯四台", 0), ("流程", "逐秒手打", 1), ("工具", "跑馬燈、包框舊標題", 2), ("資料", "時鐘與檔名差 30 秒", 0), ("管理", "記法沒統一", 1)]
    for i, (c, d, k) in enumerate(bones):
        top = i < 3
        bx, by = 0.95 + k * 3.0, (5.0 if top else 6.2)
        box(s, bx, by, 2.6, 0.6, CARD, INK, 0, lw=0.75)
        text(s, bx + 0.1, by, 0.6, 0.6, c, 12, True, RED, anchor=MSO_ANCHOR.MIDDLE)
        text(s, bx + 0.68, by, 1.9, 0.6, d, 11, False, INK, anchor=MSO_ANCHOR.MIDDLE)
        ln = s.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(bx + 2.6), Inches(by + (0.6 if top else 0)), Inches(bx + 2.85), Inches(sy))
        ln.line.color.rgb = rgb(INK)
        ln.line.width = Pt(1.0)
    return s


def s_route(prs):
    s = blank(prs)
    frame(s, "1.3 路由判斷・1.4 改善空間與商業價值", "先問能不能不用 AI，再算值多少", 4, "公式：每日時數 × 365 × 85% × 時薪 235 − 維運 22 萬。假設值，待業師確認。")
    causes = [("多台同時換題", 0), ("包框、跑馬燈干擾", 0), ("五字標要好認", 0), ("記法沒統一", 1), ("怕 AI 出錯", 2)]
    buckets = [("AI＋規則", RED), ("管理制度", INK), ("溝通", MUTE)]
    by = [1.95, 3.45, 4.35]
    for j, (b, c) in enumerate(buckets):
        label_box(s, 5.0, by[j] + (0.45 if j == 0 else 0), 2.2, 0.62, b, c, None, 13, True, "FFFFFF", radius=0.06, pad=0.04)
    for i, (t, k) in enumerate(causes):
        y = 1.95 + i * 0.6
        chip(s, 0.67, y, 3.4, t, SOFT)
        ty = by[k] + (0.45 if k == 0 else 0) + 0.31
        arrow(s, 4.1, y + 0.16, 4.97, ty, RED if k == 0 else MUTE, 1.25)
    text(s, 0.67, 5.15, 6.6, 1.5, [[("先問能不能不用 AI：", {"bold": True, "color": RED}), ("記法靠管理、擔心靠溝通；只有要同時比對多種訊號的事交給 AI。", {})]], 12, False, INK)
    text(s, 7.9, 1.85, 4.7, 0.28, "一年淨效益（萬元）", 13, True, RED)
    cd = CategoryChartData()
    cd.categories = ["每日 4 小時", "每日 8 小時", "每日 18 小時"]
    cd.add_series("萬元", (7, 36, 109))
    gf = s.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(7.9), Inches(2.2), Inches(4.77), Inches(2.9), cd)
    ch = gf.chart
    ch.has_title = False
    ch.has_legend = False
    chart_text(ch, 11)
    pl = ch.plots[0]
    pl.gap_width = 60
    pl.has_data_labels = True
    pl.data_labels.font.size = Pt(12)
    pl.data_labels.font.bold = True
    pl.series[0].format.fill.solid()
    pl.series[0].format.fill.fore_color.rgb = rgb(RED)
    va = ch.value_axis
    va.visible = False
    va.has_major_gridlines = False
    va.minimum_scale, va.maximum_scale = 0, 125
    box(s, 7.9, 5.3, 4.77, 1.1, SOFT, None, 0.08)
    text(s, 8.1, 5.35, 4.4, 1.0, [[("維持現狀的風險", {"bold": True, "color": RED})], "收視掉點算給錯新聞", "台數、時段變多，人力跟著變多"], 11.5, False, INK, space=2)
    return s


def s_tobe(prs):
    s = blank(prs)
    frame(s, "2.1 未來的工作方式・2.2 評估指標・2.3 驗證方式", "AI 先填，人只複查；四項指標驗收", 5, "負責人與週期為提案設定，待業師確認。")
    lanes = [("AI", ["讀標題", "判廣告", "判換題", "寫五字標", "對分鐘"], RED), ("人", ["", "", "看標黃格", "決定寫法", "每週更新規則"], INK)]
    for r, (who, items, c) in enumerate(lanes):
        y = 1.9 + r * 0.78
        label_box(s, 0.67, y, 0.7, 0.62, who, c, None, 13, True, "FFFFFF", radius=0.04, pad=0.02)
        for i, t in enumerate(items):
            if t:
                label_box(s, 1.47 + i * 1.08, y, 1.0, 0.62, t, SOFTRED if r == 0 else SOFT, None, 10.5, True, INK, radius=0.04, pad=0.03)
    text(s, 0.67, 3.5, 6.2, 0.3, "管理：新聞部窗口看日報；連兩週變差就退版", 11, True, MUTE)
    text(s, 7.2, 1.85, 5.4, 0.28, "2.2 評估指標（權重 100%）", 13, True, RED)
    cd = CategoryChartData()
    cd.categories = ["換題準確度 ≥0.85", "廣告準確度 ≥0.90", "自動填寫率 100%", "處理時間 隔日 9 點前"]
    cd.add_series("權重", (30, 25, 20, 25))
    gf = s.shapes.add_chart(XL_CHART_TYPE.DOUGHNUT, Inches(7.2), Inches(2.15), Inches(5.47), Inches(2.3), cd)
    ch = gf.chart
    ch.has_title = False
    chart_text(ch, 10)
    ch.has_legend = True
    ch.legend.position = XL_LEGEND_POSITION.RIGHT
    ch.legend.include_in_layout = False
    ch.legend.font.size = Pt(10)
    pl = ch.plots[0]
    pl.has_data_labels = True
    pl.data_labels.show_value = True
    pl.data_labels.number_format = '0"%"'
    pl.data_labels.number_format_is_linked = False
    pl.data_labels.font.size = Pt(11)
    pl.data_labels.font.bold = True
    pl.data_labels.font.color.rgb = rgb("FFFFFF")
    for i, c in enumerate([RED, ORANGE, "F59E0B", MUTE]):
        pt = pl.series[0].points[i]
        pt.format.fill.solid()
        pt.format.fill.fore_color.rgb = rgb(c)
    text(s, 0.67, 4.35, 6, 0.28, "2.3 驗證方式", 13, True, RED)
    steps = [("資料", "11/06 四台一小時"), ("步驟", "12:20 前調、之後只測"), ("對照組", "不用語音的版本"), ("假設", "加上語音會更準")]
    for i, (a, b) in enumerate(steps):
        x = 0.67 + i * 3.03
        station(s, x, 4.7, 2.88, 0.75, i + 1, a)
        chip(s, x, 5.55, 2.88, b)
        if i < 3:
            arrow(s, x + 2.9, 5.07, x + 3.01, 5.07, MUTE, 1.5)
    return s


AITYPE = ["VISION", "FLOW", "VISION・SPEECH", "規則＋ML", "規則＋ML", "LLM＋RAG", "FLOW"]


def s_sol(prs):
    s = blank(prs)
    frame(s, "3.1 解決方案・3.2 解法思路・3.3 採用工具・3.4 施作方式", "Q1：一個指令，四台進、「12監看」出", 6, "全程本機執行。指令：newsbar run --input-dir 資料夾。")
    qtag(s, "Q1")
    box(s, 0.67, 1.85, 12.0, 0.75, CARD, BORDER, 0.08)
    text(s, 0.87, 1.85, 11.6, 0.75, [[("輸入　", {"bold": True, "color": RED}), ("四台側錄＋空白範本　　", {}), ("輸出　", {"bold": True, "color": RED}), ("原格式「12監看」＋複查版", {})],
                                     [("不做　", {"bold": True, "color": RED}), ("收視率欄、新欄位、真偽判斷、取代側錄系統", {})]], 12, False, INK, anchor=MSO_ANCHOR.MIDDLE, space=2)
    arch = ["讀影片", "抽畫面", "辨識", "判廣告", "判換題", "寫五字標", "寫回 Excel"]
    w, g = 1.6, 0.13
    for i, t in enumerate(arch):
        x = 0.67 + i * (w + g)
        station(s, x, 2.8, w, 0.8, i + 1, t, hot=i == 6)
        if i < 6:
            arrow(s, x + w + 0.005, 3.2, x + w + g - 0.005, 3.2, RED, 1.25)
        label_box(s, x, 3.66, w, 0.26, AITYPE[i], SOFTRED, None, 9, True, RED, radius=0.04, pad=0.02)
    tools = [("看", "Apple Vision／PaddleOCR", "只讀標題列；5 秒取最多次的讀法"), ("聽", "faster-whisper", "前後 20 秒話題變了才算新的一則"),
             ("認", "聲音指紋", "認過的廣告，下次直接認出"), ("寫", "qwen2.5:7b（本機）", "寫 4–6 字，每個字查出處")]
    for i, (ic, n, d) in enumerate(tools):
        x = 0.67 + i * 3.03
        box(s, x, 4.2, 2.88, 2.2, CARD, BORDER, 0.08)
        label_box(s, x + 0.2, 4.4, 0.75, 0.75, ic, RED, None, 22, True, "FFFFFF", radius=0.37, pad=0)
        text(s, x + 1.1, 4.4, 1.7, 0.75, n, 11, True, INK, anchor=MSO_ANCHOR.MIDDLE)
        text(s, x + 0.2, 5.35, 2.5, 1.0, d, 13, False, INK)
    return s


def s_q2(prs):
    s = blank(prs)
    frame(s, "3.2 解法思路・3.4 施作方式", "Q2：只認主標題，四台各自對準分鐘", 7, "畫面：TVBS-N 11/06 12:04。例子皆為 11/06 系統產出。")
    qtag(s, "Q2")
    fx, fy, fw = 0.67, 1.85, 5.0
    pic(s, "tvbs_1204_full.jpg", fx, fy, w=fw)
    k = fw / 640
    marks = [((160, 291, 476, 29), RED, "標題列：只讀這裡"), ((160, 322, 480, 22), MUTE, "跑馬燈：不讀"), ((0, 0, 40, 290), MUTE, "左側標：不讀"), ((0, 322, 158, 22), ORANGE, "時鐘：對時")]
    for (bx, by, bw_, bh_), col, _ in marks:
        box(s, fx + bx * k, fy + by * k, bw_ * k, bh_ * k, None, col, 0, lw=2.0)
    for i, (_, col, t) in enumerate(marks):
        xx, yy = fx + (i % 2) * 2.5, 4.75 + (i // 2) * 0.36
        box(s, xx, yy + 0.07, 0.15, 0.15, col, None, 0)
        text(s, xx + 0.22, yy, 2.2, 0.3, t, 11, False, INK)
    text(s, fx, 5.6, fw, 1.3, [[("換題＝", {"bold": True, "color": RED}), ("新標題穩定 7 秒＋語音或主播佐證", {})], [("不算：", {"bold": True, "color": RED}), ("鏡位切換、LIVE 字樣", {})]], 12, False, INK, space=4)
    x, w = 6.2, 6.47
    text(s, x, 1.85, w, 0.28, "五字標", 13, True, RED)
    text(s, x, 2.15, w, 0.6, "AI 寫 4–6 字；每個字出自標題，社會新聞要有地點", 12, False, INK)
    rows = [("主標題", "五字標"), ("國三南下苑裡段貨車翻了！", "苑裡貨車翻車")]
    table(s, x, 2.75, [4.4, 2.07], [0.32, 0.4], rows, size=11)
    text(s, x, 3.7, w, 0.28, "秒級判斷，分鐘級填表（東森）", 13, True, RED)
    tx, ty, tw = x + 0.05, 4.35, w - 0.1
    per = tw / 180
    segs = [(0, 50, "台積電拉回"), (50, 94, "德州新州長"), (94, 107, ""), (107, 159, "陳奕光估反彈"), (159, 180, "台股急跌")]
    for j, (a_, b_, t) in enumerate(segs):
        box(s, tx + a_ * per, ty, (b_ - a_) * per, 0.45, SOFTRED if j % 2 == 0 else "FCA5A5", None, 0)
        if t:
            text(s, tx + a_ * per + 0.03, ty, (b_ - a_) * per, 0.45, t, 9, True, INK, anchor=MSO_ANCHOR.MIDDLE)
    cells = ["台積電拉回/德州新州長", "0050爆量股/陳奕光估反彈", "台股急跌點"]
    for m in range(3):
        box(s, tx + m * 60 * per, ty - 0.1, 0.02, 0.65, INK, None, 0)
        text(s, tx + m * 60 * per, ty - 0.35, 1.0, 0.25, f"12:0{m}", 10, True, INK)
        arrow(s, tx + (m * 60 + 30) * per, ty + 0.5, tx + (m * 60 + 30) * per, ty + 0.85, RED, 1.25)
        label_box(s, tx + m * 60 * per + 0.03, ty + 0.88, 60 * per - 0.06, 0.62, cells[m], CARD, INK, 9.5, True, INK, radius=0, pad=0.03)
    text(s, x, 6.0, w, 0.9, ["從哪一秒開始就填那一分鐘", "同分鐘多則用「/」，延續留白"], 11.5, False, INK, space=2)
    return s


def s_q3(prs):
    s = blank(prs)
    frame(s, "3.2 解法思路・3.4 施作方式", "Q3：10 種訊號認廣告，包框舊標題不採用", 8, "照片：東森 12:34–12:38 同一段廣告。")
    qtag(s, "Q3")
    groups = [("畫面", "版面縮框、台徽消失、黑畫面、時鐘消失"), ("標題列", "底色變、廣告文案、新聞預告、跑馬燈消失"), ("聲音", "廣告用語、聲音指紋")]
    for i, (g, d) in enumerate(groups):
        y = 1.9 + i * 0.62
        label_box(s, 0.67, y, 1.0, 0.5, g, SOFTRED, None, 12, True, RED, radius=0.06, pad=0.02)
        text(s, 1.8, y, 4.5, 0.5, d, 11.5, False, INK, anchor=MSO_ANCHOR.MIDDLE)
    st = ["新聞", "疑似廣告", "廣告", "疑似回新聞", "新聞"]
    bw = 1.1
    for i, n in enumerate(st):
        xx = 6.6 + i * (bw + 0.12)
        label_box(s, xx, 1.95, bw, 0.5, n, SOFT if n == "廣告" else CARD, RED if n == "廣告" else BORDER, 11, True, INK, pad=0.02)
        if i < 4:
            arrow(s, xx + bw + 0.005, 2.2, xx + bw + 0.115, 2.2, RED, 1.25)
    for i, (h, d, r) in enumerate([("進", "任兩種訊號，持續 10 秒", "填「廣告」"), ("中", "包框裡舊標題不採用", "留白"), ("回", "版面恢復＋主播或新標題", "重新填標")]):
        x = 6.6 + i * 2.05
        box(s, x, 2.75, 1.95, 1.6, CARD, BORDER, 0.08)
        label_box(s, x + 0.12, 2.87, 0.5, 0.5, h, RED, None, 14, True, "FFFFFF", radius=0.25, pad=0)
        text(s, x + 0.12, 3.45, 1.75, 0.55, d, 10.5, False, INK)
        text(s, x + 0.12, 3.98, 1.75, 0.3, r, 11, True, RED)
    for i, (f, t) in enumerate(zip(["ettv_1234_0_ad05.jpg", "ettv_1234_0_ad35.jpg", "ettv_1234_0_ad65.jpg", "ettv_1234_0_ad95.jpg"], ["12:34", "12:36", "12:37", "12:38"])):
        pic(s, f, 0.67 + i * 3.03, 4.6, w=2.88)
        text(s, 0.67 + i * 3.03, 6.25, 2.88, 0.25, t, 10, True, INK)
    text(s, 0.67, 6.55, 12, 0.3, "實測：廣告分鐘 10／10 全部抓到", 12, True, RED)
    return s


def s_res(prs):
    s = blank(prs)
    frame(s, "4.1 評估結果・4.2 結論與選擇", "Q4：四項指標全達標，加權 100 分", 9, "對照答案：TVBS-N 人工標註，逐分鐘比對。Excel 為實際產出節錄。")
    qtag(s, "Q4")
    text(s, 0.67, 1.85, 5, 0.28, "實測對目標", 13, True, RED)
    cd = CategoryChartData()
    cd.categories = ["換題準確度", "廣告準確度"]
    cd.add_series("實測", (0.885, 0.952))
    cd.add_series("目標", (0.85, 0.90))
    gf = s.shapes.add_chart(XL_CHART_TYPE.BAR_CLUSTERED, Inches(0.67), Inches(2.15), Inches(4.4), Inches(1.95), cd)
    ch = gf.chart
    ch.has_title = False
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
    ch.category_axis.reverse_order = True
    label_box(s, 0.67, 4.2, 2.1, 0.5, "填寫率 100%", SOFT, None, 12, True, INK, radius=0.06)
    label_box(s, 2.97, 4.2, 2.1, 0.5, "46 分鐘完成", SOFT, None, 12, True, INK, radius=0.06)
    label_box(s, 0.67, 4.85, 4.4, 0.85, [[("加權總分　", {"size": 13}), ("100", {"size": 28, "bold": True})]], RED, None, 12, False, "FFFFFF", radius=0.08)
    text(s, 0.67, 5.85, 4.4, 1.0, ["取捨：多跑語音換準確度", "下一步：開啟 RAG 與 ML"], 11.5, True, INK, space=3)
    pic(s, "excel_annot.png", 5.35, 1.9, w=3.6)
    text(s, 5.35, 3.72, 3.6, 0.5, "只寫四台五字標，收視率欄不動", 11, True, INK)
    pic(s, "rv_card.png", 5.35, 4.3, h=2.55)
    text(s, 7.5, 4.35, 1.6, 1.2, "複查版：標黃＋註解", 11, True, INK)
    s.shapes.add_picture(str(A / "qr_proto.png"), Inches(9.35), Inches(1.9), width=Inches(1.5))
    text(s, 11.0, 1.95, 1.7, 1.4, ["操作原型", "點一格，看那一秒"], 11, False, INK, space=2)
    pic(s, "result.png", 9.35, 3.6, w=3.3)
    return s


def s_plan(prs):
    s = blank(prs)
    frame(s, "5.1 前 90 天・5.2 潛在問題分析與預防・5.3 人與關係", "Q5：一台 Mac 跟得上全天；90 天達標才擴大", 10, "一台 Mac 處理一小時四台 46 分鐘，全天約 18.4 小時。時程為提案設定。")
    qtag(s, "Q5")
    lanes = [("前 30 天", "四台答案、統一記法", "一致性 ≥ 0.8"), ("到 60 天", "午間與人工並行", "準確度 ≥ 0.80"), ("到 90 天", "擴到晚間與全天", "準確度 ≥ 0.85")]
    lw = 3.92
    for i, (h, d, done) in enumerate(lanes):
        x = 0.67 + i * (lw + 0.12)
        box(s, x, 1.85, lw, 0.42, RED if i == 2 else INK, None, 0)
        text(s, x + 0.15, 1.85, lw - 0.3, 0.42, h, 13, True, "FFFFFF", anchor=MSO_ANCHOR.MIDDLE)
        box(s, x, 2.27, lw, 0.6, CARD, INK, 0, lw=0.75)
        text(s, x + 0.15, 2.27, lw - 0.3, 0.6, d, 12, False, INK, anchor=MSO_ANCHOR.MIDDLE)
        label_box(s, x, 2.87, lw, 0.36, "完成：" + done, SOFTRED, None, 11, True, RED, radius=0, pad=0.08, align=PP_ALIGN.LEFT)
    text(s, 0.67, 3.35, 12, 0.3, "Go／No-go：準確度低於 0.70 就停止擴大", 11, True, MUTE)
    text(s, 0.67, 3.85, 6, 0.28, "5.2 潛在問題（機率 × 衝擊）", 13, True, RED)
    gx, gy, cell = 1.5, 4.2, 0.85
    for r in range(3):
        for c in range(3):
            score = (2 - r) + c
            box(s, gx + c * cell * 1.6, gy + r * cell, cell * 1.6 - 0.04, cell - 0.04, SOFTRED if score >= 3 else (SOFT if score == 2 else "F6F6F6"), None, 0)
    for i, t in enumerate(["衝擊高", "中", "低"]):
        text(s, gx - 0.8, gy + i * cell + 0.28, 0.75, 0.3, t, 10, True, MUTE, PP_ALIGN.RIGHT)
    for i, t in enumerate(["機率低", "中", "高"]):
        text(s, gx + i * cell * 1.6, gy + 3 * cell, cell * 1.6, 0.25, t, 10, True, MUTE, PP_ALIGN.CENTER)
    pts = [("改版→重新校正", 1, 0), ("AI 中斷→規則寫標", 0, 1), ("只按「對」→每週抽查", 1, 1)]
    for t, c, r in pts:
        label_box(s, gx + c * cell * 1.6 + 0.06, gy + r * cell + 0.2, cell * 1.6 - 0.16, 0.42, t, RED, None, 9, True, "FFFFFF", radius=0.06, pad=0.02)
    text(s, 7.0, 3.85, 5.6, 0.28, "5.3 人與關係", 13, True, RED)
    ppl = [("監看員", "怕被取代", "機器填，你決定"), ("新聞部主管", "出錯誰負責", "每格有依據"), ("行政", "表格變了", "格式一格沒動")]
    for i, (who, worry, say) in enumerate(ppl):
        x = 7.0 + i * 1.92
        box(s, x, 4.2, 1.8, 2.6, CARD, BORDER, 0.08)
        label_box(s, x + 0.55, 4.35, 0.7, 0.7, who[0], INK, None, 18, True, "FFFFFF", radius=0.35, pad=0)
        text(s, x + 0.1, 5.1, 1.6, 0.3, who, 11, True, INK, PP_ALIGN.CENTER)
        text(s, x + 0.1, 5.42, 1.6, 0.3, worry, 10, False, MUTE, PP_ALIGN.CENTER)
        text(s, x + 0.1, 5.85, 1.6, 0.8, "「" + say + "」", 11, True, RED, PP_ALIGN.CENTER)
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
    for fn in (s_sum, s_prob, s_route, s_tobe, s_sol, s_q2, s_q3, s_res, s_plan):
        fn(prs)
    prs.save(out)
    print("slides:", len(prs.slides))


if __name__ == "__main__":
    main(*sys.argv[1:4])
