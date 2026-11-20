"""Build the S36 submission deck on top of the base .pptx (keeps slide 1 and 2, redraws the rest in the same style).

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
    """Question tag (Q1–Q5) on the top right, next to the page number."""
    label_box(slide, x, y, w, 0.27, label, SOFTRED, None, 10, True, RED, radius=0.13, pad=0.04)


def chart_text(chart, size=12):
    chart.font.size = Pt(size)
    chart.font.color.rgb = rgb(INK)


def blank(prs):
    layout = next(l for l in prs.slide_layouts if l.name == "Blank")
    return prs.slides.add_slide(layout)


# ---------------------------------------------------------------- slides
def s_problem(prs, s):
    """Base slide 2: keep the heat grid; add the cost of one wrong minute underneath."""
    box(s, 0.67, 0.54, 0.56, 0.06, RED, None, 0)
    for sh in s.shapes:  # base text box ran under the channel labels
        if sh.has_text_frame and sh.text_frame.text.startswith("一人同時盯四台"):
            sh.width = Inches(2.15)
        if sh.has_text_frame and sh.text_frame.text.strip() == "14":  # big number sat on its caption in PowerPoint
            sh.top = Inches(1.78)
            sh.text_frame.vertical_anchor = MSO_ANCHOR.TOP
    text(s, 0.67, 5.95, 3.0, 0.25, "錯一分鐘的後果", 13, True, RED)
    steps = [("漏記或記錯一分鐘", "監看表是隔日分析的事件索引"), ("事件與收視率錯位", "下游看不出表格錯了"), ("收視高點算給錯的新聞", "廣告破口與節目判讀都跟著錯")]
    x = 0.67
    for i, (a, b) in enumerate(steps):
        box(s, x, 6.3, 3.6, 0.68, CARD, BORDER, 0.08)
        text(s, x + 0.15, 6.36, 3.3, 0.3, a, 14, True, INK)
        text(s, x + 0.15, 6.68, 3.3, 0.25, b, 11, False, MUTE)
        if i < 2:
            arrow(s, x + 3.63, 6.64, x + 4.09, 6.64, RED)
        x += 4.12




def bar_chart_f1(s, x, y, w, h):
    cd = CategoryChartData()
    cd.categories = ["廣告分鐘 F1", "換題起始分鐘 F1"]
    cd.add_series("實測", (0.952, 0.885))
    cd.add_series("目標", (0.90, 0.85))
    gf = s.shapes.add_chart(XL_CHART_TYPE.BAR_CLUSTERED, Inches(x), Inches(y), Inches(w), Inches(h), cd)
    ch = gf.chart
    chart_text(ch, 12)
    ch.has_legend = True
    ch.legend.position = XL_LEGEND_POSITION.BOTTOM
    ch.legend.include_in_layout = False
    plot = ch.plots[0]
    plot.gap_width = 60
    plot.overlap = -10
    plot.has_data_labels = True
    plot.data_labels.number_format = "0.000"
    plot.data_labels.number_format_is_linked = False
    plot.data_labels.position = XL_LABEL_POSITION.OUTSIDE_END
    plot.data_labels.font.size = Pt(12)
    plot.data_labels.font.bold = True
    for ser, c in zip(plot.series, [RED, "A3A3A3"]):
        ser.format.fill.solid()
        ser.format.fill.fore_color.rgb = rgb(c)
    va = ch.value_axis
    va.minimum_scale, va.maximum_scale = 0, 1.1
    va.major_unit = 0.25
    va.has_major_gridlines = True
    va.major_gridlines.format.line.color.rgb = rgb(BORDER)
    va.format.line.fill.background()
    va.tick_labels.font.size = Pt(11)
    va.tick_labels.number_format = "0.00"
    va.tick_labels.number_format_is_linked = False
    ch.category_axis.tick_labels.font.size = Pt(12)
    ch.category_axis.format.line.color.rgb = rgb(BORDER)




def s_current(prs):
    s = blank(prs)
    frame(s, "問題", "現況：人工怎麼做、哪裡難", 3, "填表規則與難點整理自課程提供的 10/26 歷史監看範例與「12監看」範本。")
    tag(s, 11.15, 0.6, 0.8, "回答 Q1")
    steps = ["看四台畫面", "判斷換題或廣告", "寫五字標", "填入分鐘格", "隔日貼收視率"]
    x = 0.67
    for i, n in enumerate(steps):
        box(s, x, 1.9, 2.3, 0.5, RED if i < 4 else SOFTRED, None, 0, shape=MSO_SHAPE.PENTAGON)
        text(s, x + 0.15, 1.9, 1.95, 0.5, n, 13, True, "FFFFFF" if i < 4 else RED, PP_ALIGN.LEFT, MSO_ANCHOR.MIDDLE)
        x += 2.42
    text(s, 0.67, 2.75, 4.6, 0.3, "填好的「12監看」（節錄 12:00–12:13）", 14, True, RED)
    pic(s, "excel_sheet.png", 0.67, 3.1, w=4.6)
    text(s, 0.67, 6.5, 4.6, 0.45, "換題那一分鐘填標，延續留白，同分鐘多則用「/」，廣告填「廣告」。", 11, False, MUTE)
    text(s, 5.6, 2.75, 7, 0.3, "五個難點", 14, True, RED)
    rows = [("14 分鐘內至少 3 台同時換題", "反應不及，最容易漏記", "第 4 頁"),
            ("廣告包框裡殘留舊標題", "容易把殘字當成新聞", "第 5 頁"),
            ("同一則標題 3 分鐘內輪回", "重複填寫", "第 5 頁"),
            ("跑馬燈、快訊、記者名條", "不是這則新聞的標題", "第 5 頁"),
            ("角落小廣告", "主畫面沒變，不算廣告", "第 5 頁")]
    y = 3.15
    for a, b, c in rows:
        box(s, 5.6, y, 7.05, 0.62, CARD, BORDER, 0.08)
        text(s, 5.78, y + 0.07, 3.6, 0.26, a, 13, True, INK)
        text(s, 5.78, y + 0.34, 3.6, 0.24, b, 11, False, MUTE)
        y += 0.7
    return s


def s_solution(prs):
    s = blank(prs)
    frame(s, "解方", "解方：先判廣告、再判換題、最後填標", 4, "全程在本機執行，影片與表格不離開這台電腦。")
    tag(s, 10.4, 0.6, 1.5, "回答 Q1、Q2")
    label_box(s, 0.67, 1.95, 3.3, 0.95, [[("輸入", {"bold": True, "size": 13, "color": RED})], [("四台側錄影片＋「12監看」範本", {"size": 13})]], CARD, BORDER)
    arrow(s, 4.02, 2.42, 4.55, 2.42, RED)
    label_box(s, 4.6, 1.95, 4.1, 0.95, [[("NewsBar Copilot", {"bold": True, "size": 16, "color": "FFFFFF"})], [("本機執行，一個指令或一個網頁", {"size": 12, "color": "FFFFFF"})]], RED, RED)
    arrow(s, 8.75, 2.42, 9.28, 2.42, RED)
    label_box(s, 9.33, 1.95, 3.32, 0.95, [[("輸出", {"bold": True, "size": 13, "color": RED})], [("監看表、判斷依據、抽查清單", {"size": 13})]], CARD, BORDER)
    cards = [("01", "廣告判定", "版面、聲音、時鐘", "先凍結包框殘字"), ("02", "換題判定", "主 bar、語音、畫面", "穩定 7 秒再加佐證"), ("03", "五字標", "只用標題原字", "逐字校驗後才寫入")]
    for i, (n, a, b, c) in enumerate(cards):
        x = 0.67 + i * 4.1
        box(s, x, 3.25, 3.8, 2.0, CARD, BORDER, 0.1)
        text(s, x + 0.25, 3.4, 0.8, 0.35, n, 22, True, ORANGE)
        text(s, x + 0.25, 3.9, 3.3, 0.35, a, 20, True, INK)
        text(s, x + 0.25, 4.38, 3.3, 0.28, b, 13, False, MUTE)
        text(s, x + 0.25, 4.78, 3.3, 0.28, c, 13, True, RED)
        if i < 2:
            arrow(s, x + 3.84, 4.25, x + 4.06, 4.25, RED)
    text(s, 0.67, 5.5, 8, 0.28, "一個例子：TVBS-N 12:04", 13, True, RED)
    box(s, 0.67, 5.88, 4.9, 0.95, CARD, BORDER, 0.08)
    s.shapes.add_picture(str(A / "tvbs_1204_bar.jpg"), Inches(0.82), Inches(6.0), width=Inches(4.6))
    text(s, 0.82, 6.45, 4.6, 0.25, "畫面標題（OCR 讀到）", 11, False, MUTE)
    arrow(s, 5.62, 6.35, 6.0, 6.35, RED)
    label_box(s, 6.05, 5.88, 3.45, 0.95, [[("毒駕遭車撞飛", {"bold": True, "size": 18})], [("LLM 寫標：6 字，字都在標題裡，通過校驗", {"size": 11, "color": MUTE})]], CARD, RED, lw=1.5)
    arrow(s, 9.55, 6.35, 9.93, 6.35, RED)
    label_box(s, 9.98, 5.88, 2.67, 0.95, [[("填入 TVBS-N 12:04", {"bold": True, "size": 14})], [("「12監看」分鐘表", {"size": 11, "color": MUTE})]], SOFTRED, None)
    return s


def s_demo(prs):
    s = blank(prs)
    frame(s, "Demo", "Demo：五步完成一小時", 6, "畫面為操作原型，結果是後端 11/06 實跑輸出。複查紀錄為示範（6 格），不是實際審核結果。側錄影片由課程提供，版權屬各電視台。")
    tag(s, 10.9, 0.6, 1.0, "回答 Q4")
    names = ["1 選影片", "2 分析", "3 結果", "4 人工複查", "5 修正版"]
    x = 0.67
    for i, n in enumerate(names):
        box(s, x, 1.85, 2.3, 0.45, RED, None, 0, shape=MSO_SHAPE.PENTAGON)
        text(s, x + 0.15, 1.85, 1.95, 0.45, n, 13, True, "FFFFFF", PP_ALIGN.LEFT, MSO_ANCHOR.MIDDLE)
        x += 2.42
    pic(s, "result.png", 0.67, 2.55, w=3.3)
    text(s, 0.67, 5.15, 3.3, 0.9, "結果：逐段列出新聞與廣告，低信心標「列入抽查」。", 12, False, INK)
    pic(s, "rv_queue.png", 4.15, 2.55, h=3.85)
    pic(s, "rv_card.png", 5.92, 2.55, h=3.85)
    text(s, 4.15, 6.48, 4.9, 0.5, "複查：38 格排成佇列，每格附畫面與依據；對、改寫、不該填、先跳過。", 12, False, INK)
    pic(s, "ov_timeline.png", 9.3, 2.55, w=3.35)
    text(s, 9.3, 3.3, 3.35, 0.6, "監看台：四台一小時的新聞與廣告，橘色是廣告破口。", 11, False, INK)
    box(s, 9.3, 4.15, 3.35, 2.25, SOFT, None, 0.1)
    text(s, 9.45, 4.25, 3.1, 0.25, "掃描即可開啟", 12, True, RED)
    for i, (f, t) in enumerate([("qr_proto.png", "操作原型"), ("qr_monitor.png", "監看台"), ("qr_github.png", "原始碼")]):
        qx = 9.45 + i * 1.07
        s.shapes.add_picture(str(A / f), Inches(qx), Inches(4.6), width=Inches(0.97))
        text(s, qx, 5.62, 0.97, 0.25, t, 10, False, INK, PP_ALIGN.CENTER)
    return s


def s_engine1(prs):
    s = blank(prs)
    frame(s, "判斷引擎：廣告與換題", "先判廣告，再判換題", 5, "規則式狀態機，每個判斷都留下訊號紀錄，錯了可以追到是哪個訊號造成。東森 12:34 廣告破口：頭尾平均截 4 張。")
    tag(s, 10.4, 0.6, 1.5, "回答 Q2、Q3")
    text(s, 0.67, 1.85, 6, 0.28, "廣告狀態機", 14, True, RED)
    names = ["新聞", "疑似廣告", "廣告", "疑似回新聞", "新聞"]
    fills = [CARD, SOFTOR, SOFT, SOFTOR, CARD]
    bw, gp = 1.1, 0.325
    xs = [0.67 + i * (bw + gp) for i in range(5)]
    for xx, n, f in zip(xs, names, fills):
        label_box(s, xx, 2.2, bw, 0.55, n, f, RED if n == "廣告" else BORDER, 13, True, INK, lw=1.25, pad=0.03)
    for i in range(4):
        arrow(s, xs[i] + bw + 0.02, 2.47, xs[i + 1] - 0.02, 2.47, RED)
    conds = {0: ["2 種以上訊號", "有跑馬列的台 3 種"], 1: ["持續 25 秒以上"], 3: ["回新聞 3 項成立：", "版面恢復、語音、", "bar 新讀數"]}
    for i, c in conds.items():
        text(s, xs[i] + bw - 0.2, 2.85, 1.7 if i != 3 else 1.6, 0.8, c, 11, False, MUTE)
    text(s, 0.67, 3.55, 6.8, 0.28, "任兩種訊號成立才算廣告（共 10 種）；不到 25 秒不算，8 秒內的破口併成一段。", 12, True, INK)
    fx, fy, fw = 0.67, 4.0, 3.6
    pic(s, "tvbs_1204_full.jpg", fx, fy, w=fw)
    k = fw / 640
    for (bx, by, bw_, bh_), col in [((160, 291, 476, 29), RED), ((160, 322, 480, 22), MUTE), ((160, 268, 186, 20), MUTE), ((0, 322, 158, 22), ORANGE)]:
        box(s, fx + bx * k, fy + by * k, bw_ * k, bh_ * k, None, col, 0, lw=2.0)
    for i, (c, t) in enumerate([(RED, "主 bar：判定只讀這裡"), (MUTE, "插播字幕與跑馬燈：不採用"), (ORANGE, "畫面時鐘：用來對時")]):
        box(s, fx, 6.2 + i * 0.26, 0.16, 0.16, c, None, 0)
        text(s, fx + 0.25, 6.17 + i * 0.26, 3.6, 0.25, t, 11, False, INK)
    text(s, 4.85, 4.0, 2.6, 0.28, "換題判定", 14, True, RED)
    text(s, 4.85, 4.4, 2.6, 2.5, ["主 bar 的標題穩定 7 秒，再加 1 項佐證才算新題：語音主題改變、記者名條或主播鏡頭。", "3 分鐘內同一標題輪回，併回原本那一則。", "廣告前不到 20 秒的片段併進廣告。"], 12, False, INK, space=6)
    # ad frames
    fx = [7.75, 10.25]
    fy = [1.85, 3.5]
    frames = ["ettv_1234_0_ad05.jpg", "ettv_1234_0_ad35.jpg", "ettv_1234_0_ad65.jpg", "ettv_1234_0_ad95.jpg"]
    for i, f in enumerate(frames):
        pic(s, f, fx[i % 2], fy[i // 2], w=2.4)
    text(s, 7.75, 5.2, 4.9, 0.5, "東森 12:34 起的廣告破口。畫面版式、台徽與內容都變了，主 bar 位置沒有新聞標題。", 12, False, INK)
    box(s, 7.75, 5.85, 4.9, 1.0, SOFTRED, None, 0.08)
    text(s, 7.95, 5.92, 4.5, 0.9, ["包框期間凍結 bar 的文字，殘留的標題不採用；回新聞後重新起標。", "廣告起始那一分鐘填「廣告」。"], 12, False, INK, space=3)
    return s



def s_tech(prs):
    s = blank(prs)
    frame(s, "技術與驗證", "技術與驗證", 7, "11/06 12:00–13:00 四台共 90 格；準確度對照 TVBS 研究者單人初標（一台、約 30 個點，非正式雙人標註）。OCR：Apple Vision；語音：faster-whisper；LLM：qwen2.5:7b-instruct（本機，溫度 0）。")
    tag(s, 10.4, 0.6, 1.5, "回答 Q2、Q3、Q4")
    labs = [("標準答案（研究者初標）", "TVBS 一台，約 30 個點"), ("對照規則", "換題起始分鐘容許 ±1 分鐘"), ("調參與測試", "前 20 分鐘調參，12:20 後為測試段"), ("結果", "換題命中 23／27；廣告 10 分鐘全中")]
    w, g = 2.75, 0.33
    for i, (a, b) in enumerate(labs):
        x = 0.67 + i * (w + g)
        label_box(s, x, 1.85, w, 0.9, [[(a, {"bold": True, "size": 13, "color": RED if i == 3 else INK})], [(b, {"size": 11, "color": MUTE})]], SOFTRED if i == 3 else CARD, None if i == 3 else BORDER, pad=0.08)
        if i < 3:
            arrow(s, x + w + 0.02, 2.3, x + w + g - 0.02, 2.3, RED, 1.25)
    text(s, 0.67, 2.95, 5.6, 0.28, "準確度對目標", 13, True, RED)
    bar_chart_f1(s, 0.67, 3.2, 5.9, 2.55)
    text(s, 0.67, 5.85, 5.9, 1.0, [
        "自動填寫率 100%；四台一小時處理 46 分，目標 15 分，未達。",
        "未調參的 12:20 後測試段換題 F1 為 0.867；拿掉語音降到 0.758。"], 12, False, INK, space=3)
    text(s, 6.95, 2.95, 5.7, 0.28, "90 格的信心分布", 13, True, RED)
    cd = CategoryChartData()
    cd.categories = ["高信心 61", "FB-1 換題存疑 13", "FB-4 規則擷取 15", "FB-3 語音定名 1"]
    cd.add_series("格數", (61, 13, 15, 1))
    gf = s.shapes.add_chart(XL_CHART_TYPE.DOUGHNUT, Inches(6.95), Inches(3.2), Inches(2.9), Inches(3.0), cd)
    ch = gf.chart
    chart_text(ch, 11)
    ch.has_title = False
    ch.has_legend = False
    plot = ch.plots[0]
    plot.has_data_labels = True
    plot.data_labels.show_value = True
    plot.data_labels.font.size = Pt(11)
    plot.data_labels.font.bold = True
    plot.data_labels.font.color.rgb = rgb("FFFFFF")
    for i, c in enumerate(["A3A3A3", ORANGE, RED, DARK]):
        pt = plot.series[0].points[i]
        pt.format.fill.solid()
        pt.format.fill.fore_color.rgb = rgb(c)
    hole = plot._element.find(qn("c:holeSize"))
    if hole is None:
        hole = etree.SubElement(plot._element, qn("c:holeSize"))
    hole.set("val", "58")
    leg = [("高信心 61 格", "A3A3A3"), ("換題存疑 13 格", ORANGE), ("規則擷取 15 格", RED), ("語音定名 1 格", DARK)]
    y = 3.45
    for a, c in leg:
        box(s, 10.0, y + 0.04, 0.16, 0.16, c, None, 0, shape=MSO_SHAPE.OVAL)
        text(s, 10.3, y, 2.4, 0.26, a, 12, False, INK)
        y += 0.36
    text(s, 10.0, 4.95, 2.65, 1.3, "低信心 29 格（32%）照常填，另列抽查清單。", 12, True, RED)
    box(s, 6.95, 6.3, 5.7, 0.62, SOFTRED, None, 0.08)
    text(s, 7.1, 6.3, 5.4, 0.62, "校驗：字數 4–6、字在標題內、社會新聞要有地點。重跑 240 格逐格相同。", 11, False, INK, PP_ALIGN.LEFT, MSO_ANCHOR.MIDDLE)
    return s


def s_value(prs):
    s = blank(prs)
    frame(s, "價值", "價值：每年約 5,585 小時", 8, "以下為假設，待業師確認每日監看時數、時薪、維運成本與排班：每日 18 小時監看 × 系統接手 85% × 時薪 NT$235。")
    tag(s, 11.15, 0.6, 0.8, "回答 Q5")
    cd = CategoryChartData()
    cd.categories = ["人力價值", "維運成本", "淨效益"]
    cd.add_series("base", (0, 109, 0))
    cd.add_series("萬元", (131, 22, 109))
    gf = s.shapes.add_chart(XL_CHART_TYPE.COLUMN_STACKED, Inches(0.67), Inches(2.2), Inches(6.2), Inches(4.4), cd)
    ch = gf.chart
    chart_text(ch, 12)
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
        tf = dl.text_frame
        tf.text = lab
        r = tf.paragraphs[0].runs[0]
        r.font.size = Pt(15)
        r.font.bold = True
        r.font.color.rgb = rgb("FFFFFF")
    va = ch.value_axis
    va.minimum_scale, va.maximum_scale = 0, 140
    va.major_unit = 20
    va.has_major_gridlines = True
    va.major_gridlines.format.line.color.rgb = rgb(BORDER)
    va.format.line.fill.background()
    va.tick_labels.font.size = Pt(11)
    ch.category_axis.tick_labels.font.size = Pt(13)
    text(s, 0.67, 1.85, 6.2, 0.28, "一年的人力價值與維運成本（萬元，假設值）", 14, True, RED)
    text(s, 7.4, 1.85, 5.2, 0.28, "帶來什麼", 14, True, RED)
    vals = [("省下重複的盯盤工時", "約 5,585 小時／年，人只看低信心格。"), ("表格更一致", "換題與廣告的規則固定，不看當班的反應速度。"), ("每格可追溯", "每格留下訊號、截圖與語音節錄，錯了找得到原因。")]
    y = 2.25
    for a, b in vals:
        box(s, 7.4, y, 5.25, 1.3, CARD, BORDER, 0.08)
        text(s, 7.6, y + 0.14, 4.9, 0.3, a, 15, True, INK)
        text(s, 7.6, y + 0.52, 4.9, 0.7, b, 13, False, MUTE)
        y += 1.5
    return s


def s_risk(prs):
    s = blank(prs)
    frame(s, "風險與人機協作", "風險與人機協作", 9, "「決定權在人」：LLM 初判只用來排序，不會自動改表格；修正另存成修正版，原本的 Excel 不動。")
    tag(s, 11.15, 0.6, 0.8, "回答 Q5")
    text(s, 0.67, 1.85, 6.5, 0.28, "風險與對策", 14, True, RED)
    rows = [("標準答案只有單人初標", "補正式雙人標註，重算 F1 與 κ"), ("處理 46 分，目標 15 分", "換有 GPU 的機器實測"), ("15 格五字標靠規則擷取", "列入抽查，由人確認"), ("廣告頭尾約差 10 秒", "午間並行比較時調規則"), ("本機模型輸出不穩", "溫度 0，答案快取，重跑相同")]
    y = 2.2
    for a, b in rows:
        box(s, 0.67, y, 6.4, 0.78, CARD, BORDER, 0.08)
        text(s, 0.85, y + 0.1, 3.0, 0.6, a, 13, True, INK, anchor=MSO_ANCHOR.MIDDLE)
        arrow(s, 3.85, y + 0.39, 4.1, y + 0.39, RED, 1.25)
        text(s, 4.2, y + 0.1, 2.75, 0.6, b, 12, False, MUTE, anchor=MSO_ANCHOR.MIDDLE)
        y += 0.9
    text(s, 7.5, 1.85, 5.1, 0.28, "人機怎麼分工", 14, True, RED)
    label_box(s, 7.5, 2.2, 5.15, 0.9, [[("系統", {"bold": True, "size": 13, "color": RED})], [("90 格全由系統填寫，每格留下依據", {"size": 13})]], CARD, RED, lw=1.5)
    arrow(s, 10.07, 3.13, 10.07, 3.45, RED)
    label_box(s, 7.5, 3.5, 5.15, 0.9, [[("人", {"bold": True, "size": 13, "color": RED})], [("只看 38 格：低信心 29＋抽樣 9", {"size": 13})]], CARD, RED, lw=1.5)
    arrow(s, 10.07, 4.43, 10.07, 4.75, RED)
    label_box(s, 7.5, 4.8, 5.15, 0.9, [[("結果", {"bold": True, "size": 13, "color": RED})], [("改正另存成修正版，記審核者與原因", {"size": 13})]], SOFTRED, None)
    text(s, 7.5, 5.9, 5.15, 0.9, "LLM 只做初判與排序。要不要改，由人決定。", 13, True, INK)
    return s


def s_deploy(prs):
    s = blank(prs)
    frame(s, "上線與擴大", "上線：接在側錄系統後面", 10, "已實作：全天自動處理（newsbar watch）與每日品質日報。設計中：地端 GPU 伺服器部署、規則版本回退。放行門檻與時程待業師確認。")
    tag(s, 10.4, 0.6, 1.5, "回答 Q1、Q5")
    w, g = 2.7, 0.4
    xs = [0.67 + i * (w + g) for i in range(4)]
    top = [("三立內部側錄系統", "既有系統，不取代"), ("工作佇列", "每小時切檔，自動排入"), ("NewsBar 管線", "地端 GPU 伺服器，四台並行"), ("監看表 xlsx", "交給監看與分析")]
    for i, (a_, b_) in enumerate(top):
        label_box(s, xs[i], 1.95, w, 0.95, [[(a_, {"bold": True, "size": 14, "color": "FFFFFF" if i == 2 else INK})], [(b_, {"size": 11, "color": "FFFFFF" if i == 2 else MUTE})]], RED if i == 2 else CARD, RED if i == 2 else BORDER)
        if i < 3:
            arrow(s, xs[i] + w + 0.02, 2.42, xs[i + 1] - 0.02, 2.42, RED)
    arrow(s, xs[2] + w / 2, 2.93, xs[2] + w / 2, 3.38, RED)
    label_box(s, xs[2], 3.4, w, 0.95, [[("每日品質日報", {"bold": True, "size": 14})], [("填寫率、低信心比、OCR 空白率", {"size": 11, "color": MUTE})]], CARD, BORDER)
    arrow(s, xs[2] + w + 0.02, 3.87, xs[3] - 0.02, 3.87, RED)
    label_box(s, xs[3], 3.4, w, 0.95, [[("監看員抽查", {"bold": True, "size": 14})], [("只看低信心格與抽樣", {"size": 11, "color": MUTE})]], CARD, BORDER)
    arrow(s, xs[3] + w / 2, 4.38, xs[3] + w / 2, 4.7, ORANGE)
    label_box(s, xs[2], 4.72, 2 * w + g, 0.7, "修正紀錄 → 每週更新規則與提示詞 → 回到管線（有版本號，可退回）", SOFTOR, None, 12, True, INK)
    text(s, 0.67, 3.4, 5.6, 0.28, "為什麼這樣接", 14, True, RED)
    text(s, 0.67, 3.8, 5.8, 1.7, [
        "只接在既有側錄後面，不改現有流程。",
        "一台失敗不影響其他三台，中斷後可續跑。",
        "影片不出內網，只留文字結果；畫面截圖保留 7 天。",
        "異常由每日日報告警：填寫率、低信心比、OCR 空白率。"], 13, False, INK, space=6)
    box(s, 0.67, 5.85, 11.98, 1.0, SOFTRED, None, 0.08)
    text(s, 0.9, 5.92, 11.5, 0.9, [
        [("擴大：", {"bold": True, "color": RED}), ("先午間並行比較，F1 ≥ 0.85 且人工 ≤ 3 小時／日，再放全天四台。", {})],
        [("待業師確認：", {"bold": True, "color": RED}), ("廣告後接回同一則新聞怎麼記、節目預告算不算廣告、最晚交表時間。", {})]], 13, False, INK, space=5)
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
    for i in range(len(prs.slides) - 1, 1, -1):  # keep base slides 1 (cover) and 2 (problem)
        drop_slide(prs, i)
    s_problem(prs, prs.slides[1])
    for fn in (s_current, s_solution, s_engine1, s_demo, s_tech, s_value, s_risk, s_deploy):
        fn(prs)
    prs.save(out)
    print("slides:", len(prs.slides))


if __name__ == "__main__":
    main(*sys.argv[1:4])
