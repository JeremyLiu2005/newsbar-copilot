"""Build the S36 submission deck on top of the base .pptx (keeps the cover, draws nine pages in the UNEXT proposal structure that also answer the five official questions).

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
    rows = [("Q1 影片如何自動變成 Excel", "第 5 頁"), ("Q2 換題、字幕與五字標對時", "第 6 頁"), ("Q3 廣告包框", "第 7 頁"),
            ("Q4 Excel 呈現與驗證", "第 8、9 頁"), ("Q5 進度與擴大到全天", "第 9、10 頁")]
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


def heat(s, gx, gy, cw2, ch, show_counts=True):
    rows = heat_rows()
    cols = {"c": RED, "a": ORANGE, "": TRACK}
    counts = [0] * 60
    for r, (k, name) in enumerate(CH):
        text(s, gx - 0.85, gy + r * ch, 0.8, ch, name, 9, True, INK, anchor=MSO_ANCHOR.MIDDLE)
        for m, v in enumerate(rows[k]):
            box(s, gx + m * cw2, gy + r * ch + 0.015, cw2 - 0.025, ch - 0.03, cols[v], None, 0)
            counts[m] += v == "c"
    yb = gy + 4 * ch + 0.03
    if show_counts:
        text(s, gx - 0.85, yb, 0.8, 0.2, "同時換題", 8, True, MUTE, anchor=MSO_ANCHOR.MIDDLE)
        for m, n in enumerate(counts):
            if n >= 2:
                label_box(s, gx + m * cw2, yb, cw2 - 0.025, 0.2, str(n), RED if n >= 3 else SOFTRED, None, 7, True, "FFFFFF" if n >= 3 else RED, radius=0, pad=0)
    for m in range(0, 60, 10):
        text(s, gx + m * cw2, yb + 0.22, 0.6, 0.2, f"12:{m:02d}", 8, False, MUTE)


def station(s, x, y, w, h, n, title, hot=False):
    box(s, x, y, w, h, RED if hot else CARD, None if hot else INK, 0, lw=1.0)
    label_box(s, x + 0.08, y + 0.08, 0.26, 0.26, str(n), "FFFFFF" if hot else INK, None, 10, True, RED if hot else "FFFFFF", radius=0, pad=0)
    text(s, x + 0.12, y + 0.36, w - 0.2, h - 0.4, title, 12, True, "FFFFFF" if hot else INK, anchor=MSO_ANCHOR.MIDDLE)


def chip(s, x, y, w, t, size=9.5, fill=SOFT):
    label_box(s, x, y, w, 0.3, t, fill, None, size, False, INK, radius=0.04, pad=0.04)


def s_p2(prs):
    s = blank(prs)
    frame(s, "1.1 現況分析・1.2 真因分析", "一個人逐秒盯四台，真因是要同時比對好幾種訊號", 2, "流程整理自 11/22 說明會；熱力格為 11/06 12:00–12:59 四台系統輸出（三立側錄不納入填表）。")
    sts = [("側錄四台", ["四台各錄一小時"]), ("逐秒看、判斷", ["一人同時盯四台", "包框留著舊標題"]), ("寫五字標", ["要讓人認得是哪則"]),
           ("填入分鐘格", ["漏記、時間錯位"]), ("隔日灌收視率", ["錯一分鐘就算錯新聞"])]
    w, g = 2.2, 0.25
    for i, (t, probs) in enumerate(sts):
        x = 0.67 + i * (w + g)
        station(s, x, 1.82, w, 0.72, i + 1, t, hot=i == 1)
        if i < 4:
            arrow(s, x + w + 0.02, 2.18, x + w + g - 0.02, 2.18, MUTE, 1.5)
        for j, pr in enumerate(probs):
            chip(s, x, 2.62 + j * 0.34, w, pr, fill=SOFTRED if i == 1 else SOFT)
    text(s, 0.67, 3.38, 8, 0.25, "11/06 午間一小時，四台每分鐘的狀態（紅＝換題、橘＝廣告）", 11, True, RED)
    heat(s, 1.5, 3.66, 0.12, 0.18)
    for i, (a_, b_) in enumerate([("25 分鐘", "兩台以上同一分鐘換題"), ("6 分鐘", "三台以上同一分鐘換題")]):
        y = 3.42 + i * 0.66
        box(s, 9.05, y, 3.62, 0.58, CARD, BORDER, 0.06)
        text(s, 9.2, y, 1.6, 0.58, a_, 18, True, RED, anchor=MSO_ANCHOR.MIDDLE)
        text(s, 10.8, y, 1.85, 0.58, b_, 10, False, INK, anchor=MSO_ANCHOR.MIDDLE)
    text(s, 0.67, 4.82, 6, 0.25, "真因分析（魚骨圖）", 11, True, RED)
    sy = 5.92
    box(s, 0.8, sy - 0.02, 8.9, 0.04, INK, None, 0)
    label_box(s, 9.75, sy - 0.48, 2.92, 0.96, [[("漏記、時間錯位", {"bold": True})], [("收視檢討算錯新聞", {"size": 10})]], RED, None, 12, False, "FFFFFF", radius=0.08, pad=0.06)
    bones = [("人", "一人盯四台；離席、疲勞就漏", 0), ("流程", "逐秒手打；隔日才發現錯", 1), ("工具", "畫面有跑馬燈、左側標、包框舊標題", 2),
             ("資料", "畫面時鐘與檔名時間差 23–32 秒", 0), ("管理", "廣告後接回、節目預告記法沒統一", 1)]
    for i, (c, d, k) in enumerate(bones):
        top = i < 3
        bx = 0.95 + k * 3.0
        by = 5.1 if top else 6.18
        box(s, bx, by, 2.6, 0.62, CARD, INK, 0, lw=0.75)
        text(s, bx + 0.1, by, 0.55, 0.62, c, 12, True, RED, anchor=MSO_ANCHOR.MIDDLE)
        text(s, bx + 0.62, by, 1.93, 0.62, d, 9.5, False, INK, anchor=MSO_ANCHOR.MIDDLE)
        ln = s.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(bx + 2.6), Inches(by + (0.62 if top else 0)), Inches(bx + 2.85), Inches(sy))
        ln.line.color.rgb = rgb(INK)
        ln.line.width = Pt(1.0)
    return s


def s_p3(prs):
    s = blank(prs)
    frame(s, "1.3 路由判斷・1.4 改善空間與商業價值", "先問能不能不用 AI，再算改好值多少", 3, "金額為假設，待業師確認：時薪 NT$235（2026 年基本時薪 196 元）、系統接手 85%、維運每年 22 萬；每日監看時數尚無資料，列三種情況。")
    text(s, 0.67, 1.82, 6, 0.25, "1.3 每個真因，能不能不用 AI？", 12, True, RED)
    rows = [("真因", "處理方式", "理由"),
            ("多台同一分鐘換題，一人看不完", "AI", "加人只能分攤，判斷仍靠反應速度；機器四台同時看"),
            ("包框殘字、跑馬燈、左側標干擾", "AI＋規則", "要同時比對多種畫面訊號，規則寫清楚交給機器最穩"),
            ("五字標要好認又不能亂編", "AI＋檢查", "AI 濃縮，程式逐字檢查出處，不過就換規則摘字"),
            ("記法沒統一（廣告後接回、節目預告）", "先管理", "不需要 AI，請業師訂一次統一記法"),
            ("擔心 AI 出錯或取代人", "溝通", "人保留最終決定，系統只排出要看的格子")]
    table(s, 0.67, 2.12, [3.7, 1.4, 6.9], [0.32] + [0.37] * 5, rows, size=11)
    text(s, 0.67, 4.5, 8, 0.25, "1.4 改好了，每年值多少？（萬元）", 12, True, RED)
    rows = [("改善項目", "現況與算法", "改善空間（全部解決）", "預期目標", "商業價值"),
            ("監看人力・每日 4 小時", "4 小時 × 365 天 × 235 元", "34 萬", "系統接手 85%，扣維運 22 萬", "7 萬"),
            ("監看人力・每日 8 小時", "8 小時 × 365 天 × 235 元", "69 萬", "同上", "36 萬"),
            ("監看人力・每日 18 小時", "18 小時 × 365 天 × 235 元", "154 萬", "同上", "109 萬"),
            ("收視檢討算錯新聞", "錯一分鐘，收視掉點算給錯的新聞", "算不成錢", "台別×分鐘自動對齊", "非量化")]
    table(s, 0.67, 4.8, [2.4, 3.3, 2.1, 2.7, 1.5], [0.3] + [0.4] * 4, rows, size=10.5)
    return s


def s_p4(prs):
    s = blank(prs)
    frame(s, "2.1 未來場景・2.2 可能的方案・2.3 可行性分析", "六個做法，先做影響大又馬上能做的", 4, "時程與分工為提案設定，待業師確認。象限依影響度與資源準備度排列。")
    text(s, 0.67, 1.82, 8, 0.25, "2.1 問題解決以後：每一站都有 AI 做的事，也有人做的事", 12, True, RED)
    rows = [("", "側錄交檔", "判斷換題與廣告", "寫五字標", "填表", "隔日灌收視率"),
            ("AI 做", "每小時自動切檔、排入處理", "四台同時判斷，留下每格依據", "濃縮 4–6 字，逐字檢查", "對分鐘寫回原表，另存複查版", "—"),
            ("人做", "—", "看標黃的格子", "決定最後寫法", "按「對」或改寫", "照常灌入，格式沒變")]
    table(s, 0.67, 2.12, [0.9, 2.1, 2.4, 2.1, 2.4, 2.1], [0.3, 0.38, 0.38], rows, size=10)
    text(s, 0.67, 3.35, 6, 0.25, "2.2 每個真因的方案", 12, True, RED)
    rows = [("真因", "方案"),
            ("多台同時換題", "四台平行判斷：標題穩定 7 秒＋語音、主播鏡頭等佐證"),
            ("包框與字幕干擾", "只讀標題列；10 種訊號判廣告；聲音指紋認廣告"),
            ("五字標品質", "AI 寫標＋逐字檢查＋規則備援"),
            ("對時錯位", "讀畫面時鐘；秒級判斷、分鐘級填表"),
            ("沒把握的格子", "複查版 Excel 標黃附註解＋複查台"),
            ("記法沒統一", "業師訂記法（管理制度）")]
    table(s, 0.67, 3.65, [1.75, 4.6], [0.3] + [0.47] * 6, rows, size=10.5)
    text(s, 7.3, 3.35, 5.3, 0.25, "2.3 可行性：影響度 × 資源準備度", 12, True, RED)
    qx, qy, qw, qh = 7.75, 3.68, 2.45, 1.6
    quads = [("影響大・還沒準備好", ["顯示卡機四台並行", "全天自動交檔"], SOFT), ("影響大・馬上能做（已做）", ["只讀標題列", "廣告 10 種訊號", "秒級判斷分鐘填表", "AI 寫標＋逐字檢查", "複查版 Excel"], SOFTRED),
             ("影響小・還沒準備好", ["全台廣告聲音庫"], "F6F6F6"), ("影響小・馬上能做", ["四台同一事件同名"], "F6F6F6")]
    for i, (h, items, f) in enumerate(quads):
        x, y = qx + (i % 2) * (qw + 0.05), qy + (i // 2) * (qh + 0.05)
        box(s, x, y, qw, qh, f, RED if i == 1 else None, 0, lw=1.5)
        text(s, x + 0.1, y + 0.06, qw - 0.2, 0.25, h, 9.5, True, RED)
        text(s, x + 0.1, y + 0.35, qw - 0.2, qh - 0.4, items, 9.5, False, INK, space=1)
    text(s, qx - 0.45, qy + 0.6, 0.4, 0.5, "影響大", 9, True, MUTE)
    text(s, qx - 0.45, qy + qh + 0.65, 0.4, 0.5, "影響小", 9, True, MUTE)
    return s


def s_q2(prs):
    s = blank(prs)
    frame(s, "3.1 技術路線表②　換題、字幕與五字標對時", "Q2：只認主標題，四台各自對準分鐘", 6, "畫面為 TVBS-N 11/06 12:04 實際側錄；例子皆為 11/06 系統產出。表上時間依各台畫面時鐘，不依側錄檔名（兩者差 23–32 秒）。")
    qtag(s, "Q2")
    fx, fy, fw = 0.67, 1.85, 4.3
    pic(s, "tvbs_1204_full.jpg", fx, fy, w=fw)
    k = fw / 640
    marks = [((160, 291, 476, 29), RED, "主標題 bar：唯一讀取"), ((160, 322, 480, 22), MUTE, "跑馬燈：不採用"),
             ((160, 268, 186, 20), MUTE, "插播字幕、角標：不採用"), ((0, 0, 40, 290), MUTE, "左側標、天氣：不採用"), ((0, 322, 158, 22), ORANGE, "畫面時鐘：對時")]
    for (bx, by, bw_, bh_), col, _ in marks:
        box(s, fx + bx * k, fy + by * k, bw_ * k, bh_ * k, None, col, 0, lw=1.75)
    for i, (_, col, t) in enumerate(marks):
        y = fy + 0.05 + i * 0.47
        box(s, fx + fw + 0.12, y + 0.05, 0.14, 0.14, col, None, 0)
        text(s, fx + fw + 0.32, y, 1.55, 0.42, t, 9.5, False, INK)
    text(s, 0.67, 4.42, 6.1, 0.25, "換題怎麼判斷", 12, True, RED)
    text(s, 0.67, 4.72, 6.1, 2.2, [[("換題＝", {"bold": True}), ("標題列換新標題、穩定 7 秒，再加任一佐證：前後 20 秒語音話題不同、前後 5 秒主播鏡頭、說話的人換了、25 秒內記者名條", {})],
                                   [("不算換題：", {"bold": True}), ("鏡位切換（主播→SOT→LIVE→假連→說新聞）、LIVE／直播字樣、只閃 5 秒以內的名條", {})],
                                   [("併回原本那則：", {"bold": True}), ("3 分鐘內重複出現的標題；沒佐證的第二段標題", {})]], 10.5, False, INK, space=4)
    x, w = 7.0, 5.67
    text(s, x, 1.82, w, 0.25, "五字標怎麼寫", 12, True, RED)
    text(s, x, 2.1, w, 1.2, ["只用這則的主標題＋語音；AI 寫 4–6 字，濃縮人、事、地，不硬切前五字", "逐字檢查：每個字出自來源；社會新聞要有地點；「疑、涉、傳」要保留",
                             "沒過重寫一次；再沒過或 AI 沒回應，改用規則濃縮並標黃"], 10.5, False, INK, space=2)
    rows = [("主標題", "五字標"), ("國三南下苑裡段貨車翻了！大量木材噴飛車道", "苑裡貨車翻車"), ("涉助共諜案張俊傑偷渡船長.5漁工遭押", "涉助共諜案")]
    table(s, x, 3.35, [4.1, 1.57], [0.28, 0.32, 0.32], rows, size=10)
    text(s, x, 4.42, w, 0.25, "秒級判斷，分鐘級填表", 12, True, RED)
    rows = [("台・分鐘", "填入的五字標"), ("東森 12:00", "台積電拉回/德州新州長（12:00:50 換題）"), ("東森 12:01", "0050爆量股/陳奕光估反彈"),
            ("民視 12:01–04", "留白：同一則延續")]
    table(s, x, 4.72, [1.4, 4.27], [0.28, 0.32, 0.32, 0.32], rows, size=10)
    text(s, x, 6.05, w, 0.9, ["從哪一秒開始就填那一分鐘；同分鐘多則依序用「/」；延續留白", "廣告起始那分鐘填「廣告」；四台各讀自己的畫面時鐘"], 10, False, INK, space=2)
    return s


def s_p8(prs):
    s = blank(prs)
    frame(s, "3.2 建置方式・3.3 試驗計劃", "誰負責每天的複查，怎樣算有效", 8, "R 負責執行、A 最後負責、C 事前要問、I 事後要知道；一件事只有一個 A。分工為提案設定，待業師確認。")
    qtag(s, "Q4")
    text(s, 0.67, 1.82, 8, 0.25, "3.2 管理機制：每日複查與每週規則更新（RACI）", 12, True, RED)
    rows = [("工作項目", "監看員", "新聞部窗口", "創造智能／IT", "行政"),
            ("每天看複查版標黃格，按對、改寫或不該填", "R", "A", "", "I"),
            ("每天看品質日報（填寫率、標黃比例）", "I", "A", "R", ""),
            ("每週依修正紀錄更新規則，連兩週沒進步就退回上一版", "C", "A", "R", ""),
            ("電視台改版時，用校正頁重框標題列位置", "I", "A", "R", ""),
            ("隔天 9 點依台別×分鐘灌收視率", "", "I", "", "R")]
    table(s, 0.67, 2.12, [5.6, 1.5, 1.6, 1.8, 1.5], [0.32] + [0.38] * 5, rows, size=10.5)
    text(s, 0.67, 4.55, 8, 0.25, "3.3 試驗計劃：先定怎樣算有效", 12, True, RED)
    rows = [("方案", "看什麼指標", "做到多少算有效", "輸入資料", "測試方法"),
            ("換題判斷", "換題準確度（起始分鐘差 1 分內算對）", "≥ 0.85", "11/06 四台一小時＋TVBS 人工答案", "前 20 分鐘調規則，之後只測"),
            ("廣告判定", "廣告分鐘準確度", "≥ 0.90", "同上", "同上"),
            ("一鍵填表", "自動填寫率；原表其他格不變", "100%；逐格比對全過", "空白「12監看」範本", "寫回後比對值與格式"),
            ("處理速度", "四台一小時處理時間", "隔日 9 點前完成", "同上", "Mac 實跑計時")]
    table(s, 0.67, 4.85, [1.3, 3.3, 1.9, 3.0, 2.5], [0.3] + [0.4] * 4, rows, size=10)
    return s


def s_p9(prs):
    s = blank(prs)
    frame(s, "4.1 初步結果・4.2 預期效益・4.3 後續優化建議", "四項目標全部達成，加上語音最關鍵", 9, "對照答案：TVBS-N 午間一小時人工標註，逐分鐘比對。Excel 為 11/06 實際產出節錄（12:05 到 12:34 之間省略）。")
    qtag(s, "Q4・Q5")
    text(s, 0.67, 1.82, 6, 0.25, "4.1 試驗結果總表", 12, True, RED)
    rows = [("試驗的方案", "預期目標", "實際結果", "達成度"),
            ("換題判斷", "準確度 0.85", "0.885（換題點找到 23／27）", "達成"),
            ("廣告判定", "準確度 0.90", "0.952（廣告分鐘 10／10）", "達成"),
            ("一鍵填表", "100%、原表不變", "90 格全自動，逐格比對通過", "達成"),
            ("處理速度", "隔日 9 點前", "一台 Mac 46 分鐘", "達成")]
    table(s, 0.67, 2.12, [1.3, 1.6, 3.0, 0.8], [0.3] + [0.36] * 4, rows, size=10.5)
    cd = CategoryChartData()
    cd.categories = ["只看標題文字", "加上語音"]
    cd.add_series("換題準確度", (0.758, 0.885))
    gf = s.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(7.75), Inches(2.1), Inches(2.4), Inches(1.95), cd)
    ch = gf.chart
    ch.has_title = False
    ch.has_legend = False
    chart_text(ch, 9)
    pl = ch.plots[0]
    pl.gap_width = 50
    pl.has_data_labels = True
    pl.data_labels.number_format = "0.000"
    pl.data_labels.number_format_is_linked = False
    pl.data_labels.font.size = Pt(10)
    pl.data_labels.font.bold = True
    ser = pl.series[0]
    ser.format.fill.solid()
    ser.format.fill.fore_color.rgb = rgb(RED)
    pt = ser.points[0]
    pt.format.fill.solid()
    pt.format.fill.fore_color.rgb = rgb("D4D4D4")
    va = ch.value_axis
    va.minimum_scale, va.maximum_scale = 0.6, 1.0
    va.visible = False
    va.has_major_gridlines = False
    ch.category_axis.tick_labels.font.size = Pt(9)
    text(s, 10.3, 2.15, 2.37, 1.9, [[("關鍵發現", {"bold": True, "color": RED})], "語音是換題判斷最關鍵的一環：只看標題文字 0.758，加上語音 0.885。"], 10.5, False, INK, space=4)
    text(s, 0.67, 4.15, 6.8, 0.25, "Excel 呈現：只填四台五字標，收視率欄原封不動", 12, True, RED)
    pic(s, "excel_annot.png", 0.67, 4.45, w=4.4)
    text(s, 5.2, 4.45, 2.3, 2.45, ["只開「12監看」分頁，只寫值", "A30、M30、A4+ 不碰", "寫完逐格比對值與格式", "另附複查版：標黃＋註解寫影片時間、標題原文、語音、原因"], 9.5, False, INK, space=3)
    x, w = 7.75, 4.92
    text(s, x, 4.15, w, 0.25, "4.2 預期效益", 12, True, RED)
    rows = [("效益來源", "效益", "推估算法"),
            ("監看人力", "每年 7–109 萬", "每日 4–18 小時 × 85%（見第 3 頁）"),
            ("收視檢討", "不再算錯新聞", "台別×分鐘自動對齊（非量化）")]
    table(s, x, 4.45, [1.1, 1.45, 2.37], [0.28, 0.36, 0.36], rows, size=9.5)
    text(s, x, 5.55, w, 0.25, "4.3 要走到商用，接下來", 12, True, RED)
    rows = [("優化方向", "具體建議"),
            ("換成真實資料", "三立提供四台各一小時、兩位監看員的答案"),
            ("讓人真的用", "午間與人工並行兩週，比對兩邊結果"),
            ("加快", "顯示卡機四台同時處理，目標 15 分鐘")]
    table(s, x, 5.85, [1.3, 3.62], [0.27, 0.27, 0.27, 0.27], rows, size=9.5)
    return s


def s_p10(prs):
    s = blank(prs)
    frame(s, "5.1 前 90 天・5.2 潛在問題分析與預防・5.3 人與關係", "90 天三波，達標才擴大", 10, "時程與門檻為提案設定，待業師確認。處理速度換算：一台 Mac 每處理一小時四台影片 46 分鐘，全天 24 小時四台約 18.4 小時。")
    qtag(s, "Q5")
    lanes = [("前 30 天", "先把答案與記法定下來", ["四台各一小時、兩位監看員標註", "業師訂廣告後接回、節目預告記法", "側錄每小時自動交檔"], "兩位標註者一致性 ≥ 0.8"),
             ("到 60 天", "午間和人工並行", ["每天午間系統與人工各做一份", "顯示卡機實測四台同時處理"], "準確度 ≥ 0.80，四台一小時 ≤ 15 分鐘"),
             ("到 90 天", "擴到晚間與全天", ["午間＋晚間正式交付", "一台 Mac 每天約 18.4 小時跑完全天四台；全部本機、不用 API"], "準確度 ≥ 0.85，人工 ≤ 3 小時／日")]
    lw = 3.92
    for i, (h, sub, items, done) in enumerate(lanes):
        x = 0.67 + i * (lw + 0.12)
        box(s, x, 1.82, lw, 0.42, RED if i == 2 else INK, None, 0)
        text(s, x + 0.12, 1.82, lw - 0.2, 0.42, [[(h + "　", {"bold": True, "size": 13}), (sub, {"size": 10.5})]], 11, False, "FFFFFF", anchor=MSO_ANCHOR.MIDDLE)
        box(s, x, 2.24, lw, 1.18, CARD, INK, 0, lw=0.75)
        text(s, x + 0.12, 2.3, lw - 0.24, 1.1, items, 10, False, INK, space=2)
        label_box(s, x, 3.42, lw, 0.34, "怎樣算完成：" + done, SOFTRED, None, 9.5, True, RED, radius=0, pad=0.06, align=PP_ALIGN.LEFT)
    text(s, 0.67, 3.86, 12, 0.25, "Go／No-go：每一波達標才進下一波；準確度低於 0.70 就停止擴大、回頭調規則。", 10, True, MUTE)
    text(s, 0.67, 4.2, 6, 0.25, "5.2 做了以後會出什麼事，怎麼預防", 12, True, RED)
    rows = [("潛在問題", "預防措施"),
            ("電視台改版，標題列位置變了", "品質日報讀取率突降就告警；校正頁重框一次"),
            ("監看員只按「對」，沒細看", "每週抽 10 格和人工答案比對"),
            ("AI 服務中斷", "自動改用規則寫標並標黃，流程不停"),
            ("記法爭議（廣告後接回同一則）", "請業師訂一次，寫進規則")]
    table(s, 0.67, 4.5, [2.9, 3.4], [0.3] + [0.4] * 4, rows, size=10)
    text(s, 7.2, 4.2, 5.4, 0.25, "5.3 誰擔心什麼？怎麼跟他說", 12, True, RED)
    rows = [("關鍵人物", "他的顧慮", "溝通方式（一句話）"),
            ("監看員", "被取代、多一份工", "「機器填，你決定；標黃的才需要你看。」"),
            ("新聞部主管", "AI 出錯誰負責", "「每格有依據，改過留紀錄，原表不動。」"),
            ("行政", "表格變了", "「格式一格都沒動，照常灌收視率。」"),
            ("創造智能", "維運負擔", "「只接檔案，不改側錄系統；規則可退版。」")]
    table(s, 7.2, 4.5, [1.15, 1.5, 2.82], [0.3] + [0.4] * 4, rows, size=9.5)
    return s


def s_q1(prs):
    s = blank(prs)
    frame(s, "3.1 技術路線表①　影片如何從頭到尾變成 Excel", "Q1：一個指令，四台影片進、填好的「12監看」出", 5, "全程在本機執行，影片不離開三立。分 Mac 版與 NVIDIA 顯示卡版，GitHub 兩種版本皆可下載。")
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
    text(s, 0.67, 4.3, 12, 0.28, "每個工具怎麼做決定　｜　拿掉標題列讀取，廣告、換題、五字標全部跟著垮", 13, True, RED)
    rows = [("工具", "負責", "怎麼做決定"),
            ("Apple Vision／PaddleOCR", "讀標題列、時鐘", "只讀各台設定好的標題列位置；同一標題 5 秒內取出現最多次的讀法，單張讀錯不影響"),
            ("faster-whisper", "語音轉文字", "比對換題點前後 20 秒的話題；話題沒變就不算新的一則"),
            ("自建聲音指紋", "認出已知廣告", "確認過的廣告聲音自動入庫；同一支廣告在別台、別天出現直接命中"),
            ("qwen2.5:7b（本機）", "寫五字標", "溫度 0＋結果快取，同一支影片每次結果相同；寫出的字逐一回查標題原文"),
            ("openpyxl", "寫回 Excel", "依表頭文字找到時間列與四台五字標欄，只寫值，其他格子不碰")]
    table(s, 0.67, 4.62, [2.3, 1.7, 8.0], [0.32] + [0.4] * 5, rows, size=11)
    return s


def s_q3(prs):
    s = blank(prs)
    frame(s, "3.1 技術路線表③　廣告包框如何辨認、記錄與恢復新聞", "Q3：廣告看 10 種訊號，包框裡的舊標題一律不採用", 7, "參數為 11/06 實跑設定。照片：東森 12:34:46–12:38:44 同一段廣告破口，平均截 4 張。")
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
    for fn in (s_p2, s_p3, s_p4, s_q1, s_q2, s_q3, s_p8, s_p9, s_p10):
        fn(prs)
    prs.save(out)
    print("slides:", len(prs.slides))


if __name__ == "__main__":
    main(*sys.argv[1:4])
