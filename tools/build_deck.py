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
    stats = [("240 格", "每小時要判斷的格數：4 台 × 60 分鐘"), ("約 5 字", "每格要寫出讓人認得是哪則新聞的標題"), ("隔日", "監看表要貼收視率；錯位就會判錯節目")]
    for i, (a, b) in enumerate(stats):
        x = 0.67 + i * 4.065
        box(s, x, 6.0, 3.85, 0.95, CARD, BORDER, 0.08)
        text(s, x + 0.2, 6.0, 1.45, 0.95, a, 22, True, ORANGE if i < 2 else RED, anchor=MSO_ANCHOR.MIDDLE)
        text(s, x + 1.65, 6.0, 2.05, 0.95, b, 12, False, INK, anchor=MSO_ANCHOR.MIDDLE)


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
    tag(s, 11.15, 0.6, 0.8, "回答 Q2")
    text(s, 0.67, 1.95, 6.2, 0.3, "同一分鐘（12:00），四台同時在播", 14, True, RED)
    for i, (ch_, n_) in enumerate([("tvbs", "TVBS-N"), ("ettv", "東森"), ("ftv", "民視"), ("cts", "華視")]):
        x_, y_ = 0.67 + (i % 2) * 3.2, 2.35 + (i // 2) * 2.0
        pic(s, f"{ch_}_1200_full.jpg", x_, y_, w=3.0)
        text(s, x_, y_ + 1.72, 3.0, 0.25, f"{n_}　12:00", 12, True, INK)
    text(s, 0.67, 6.42, 6.2, 0.5, "一個人同時盯四台，每分鐘各填一格。", 14, True, INK)
    text(s, 7.3, 1.95, 5.3, 0.3, "五個難點", 14, True, RED)
    rows = [("14 分鐘內至少 3 台同時換題", "反應不及，最容易漏記"), ("廣告包框裡殘留舊標題", "容易把殘字當成新聞"), ("同一則標題 3 分鐘內重複出現", "重複填寫"), ("跑馬燈、快訊、記者名條", "不是這則新聞的標題"), ("角落小廣告", "主畫面沒變，不算廣告")]
    y = 2.35
    for a, b in rows:
        box(s, 7.3, y, 5.35, 0.78, CARD, BORDER, 0.08)
        text(s, 7.5, y + 0.1, 4.9, 0.3, a, 14, True, INK)
        text(s, 7.5, y + 0.43, 4.9, 0.28, b, 12, False, MUTE)
        y += 0.9
    return s


def s_solution(prs):
    s = blank(prs)
    frame(s, "解方", "解方：先判廣告、再判換題、最後填標", 4, "一個指令或一個網頁就能跑完整條路徑。全程在本機執行，影片與表格不離開這台電腦。")
    tag(s, 9.9, 0.6, 2.0, "回答 Q1、Q2、Q4")
    pipe = [("抽畫面與音軌", "FFmpeg"), ("畫面時鐘校時", "四台時鐘都讀得到"), ("讀三種訊號", "OCR、語音、畫面"), ("廣告判定", "10 種訊號"), ("換題判斷", "穩定 7 秒＋佐證"), ("五字標", "LLM＋逐字檢查"), ("寫回 Excel", "秒轉分鐘格，只動四欄")]
    pw, pg = 1.53, 0.2
    for i, (a_, b_) in enumerate(pipe):
        x = 0.67 + i * (pw + pg)
        label_box(s, x, 1.95, pw, 0.95, [[(a_, {"bold": True, "size": 12, "color": "FFFFFF" if i == 6 else INK})], [(b_, {"size": 10, "color": "FFFFFF" if i == 6 else MUTE})]], RED if i == 6 else CARD, RED if i == 6 else BORDER, pad=0.04)
        if i < 6:
            arrow(s, x + pw + 0.01, 2.42, x + pw + pg - 0.01, 2.42, RED, 1.25)
    text(s, 0.67, 3.1, 6.5, 0.28, "輸出：填好的「12監看」（節錄）", 13, True, RED)
    pic(s, "excel_annot.png", 0.67, 3.45, w=6.5)
    text(s, 0.67, 6.65, 6.5, 0.3, "只寫四台五字標欄；寫入前後逐格比對，其他格不變。", 11, False, MUTE)
    lx = 7.45
    text(s, lx, 3.1, 5.2, 0.28, "怎麼看這張表", 13, True, RED)
    leg = [(None, RED, "換題那一分鐘填標（紅框：TVBS-N 12:04）"), ("FFEDD5", BORDER, "同一分鐘換了好幾則，用「/」連起來"), ("F7E3C8", BORDER, "廣告起始那一分鐘填「廣告」"), (CARD, BORDER, "空白＝延續同一則")]
    for i, (fill_, line_, t_) in enumerate(leg):
        yy = 3.45 + i * 0.38
        box(s, lx, yy + 0.02, 0.26, 0.22, fill_, line_, 0, lw=2.5 if i == 0 else 1.0)
        text(s, lx + 0.4, yy, 4.8, 0.28, t_, 12, False, INK)
    text(s, lx, 5.1, 5.2, 0.28, "紅框那格怎麼來", 13, True, RED)
    box(s, lx, 5.45, 5.2, 0.55, CARD, BORDER, 0.06)
    s.shapes.add_picture(str(A / "tvbs_1204_bar.jpg"), Inches(lx + 0.1), Inches(5.54), width=Inches(5.0))
    arrow(s, lx + 2.6, 6.02, lx + 2.6, 6.14, RED, 1.25)
    label_box(s, lx, 6.16, 5.2, 0.38, "LLM 寫標：毒駕遭車撞飛（6 字，字都在標題裡）", CARD, RED, 12, True, INK, radius=0.06, lw=1.5)
    arrow(s, lx + 2.6, 6.56, lx + 2.6, 6.68, RED, 1.25)
    label_box(s, lx, 6.7, 5.2, 0.32, "填入 TVBS-N 12:04", SOFTRED, None, 12, True, RED, radius=0.06)
    return s


def s_demo(prs):
    s = blank(prs)
    frame(s, "Demo", "Demo：五步完成一小時", 6, "畫面為流程示範，結果是後端 11/06 實跑輸出。複查紀錄為示範（6 格），不是實際審核結果。側錄影片由課程提供，版權屬各電視台。")
    tag(s, 10.9, 0.6, 1.0, "回答 Q4")
    for x, w, t in [(0.67, 2.45, "1 選影片　2 分析"), (3.3, 3.0, "3 結果"), (6.5, 4.5, "4 人工複查　5 修正版"), (11.25, 1.4, "掃描開啟")]:
        label_box(s, x, 1.9, w, 0.4, t, RED, None, 13, True, "FFFFFF", radius=0.2, pad=0.05)
    pic(s, "upload.png", 0.67, 2.5, w=2.45)
    pic(s, "progress.png", 0.67, 4.7, w=2.45)
    text(s, 0.67, 6.15, 2.45, 0.7, "選四台與範本；四台並行，各自顯示階段。", 11, False, INK)
    pic(s, "result.png", 3.3, 2.5, w=3.0)
    text(s, 3.3, 4.9, 3.0, 1.2, "逐段列出新聞與廣告，低信心標「列入抽查」，可請本機模型先初判換題。", 12, False, INK)
    pic(s, "rv_queue.png", 6.5, 2.5, h=3.7)
    pic(s, "rv_card.png", 8.13, 2.5, h=3.7)
    text(s, 6.5, 6.3, 4.5, 0.6, "38 格排成清單，每格附畫面與依據；對、改寫、不該填、先跳過。複查完另存修正版。", 11, False, INK)
    for i, (f, t) in enumerate([("qr_proto.png", "流程示範"), ("qr_monitor.png", "結果網頁"), ("qr_github.png", "原始碼")]):
        qy = 2.5 + i * 1.5
        s.shapes.add_picture(str(A / f), Inches(11.4), Inches(qy), width=Inches(1.1))
        text(s, 11.25, qy + 1.12, 1.4, 0.25, t, 10, False, INK, PP_ALIGN.CENTER)
    return s


def s_engine1(prs):
    s = blank(prs)
    frame(s, "判斷引擎：廣告與換題", "先判廣告，再判換題", 5, "規則式判斷，每個判斷都留下訊號紀錄，錯了可以追到是哪個訊號造成。")
    tag(s, 10.4, 0.6, 1.5, "回答 Q2、Q3")
    text(s, 0.67, 1.85, 6, 0.28, "廣告判定流程", 14, True, RED)
    names = ["新聞", "疑似廣告", "廣告", "疑似回新聞", "新聞"]
    fills = [CARD, SOFTOR, SOFT, SOFTOR, CARD]
    bw, gp = 1.1, 0.325
    xs = [0.67 + i * (bw + gp) for i in range(5)]
    for xx, n, f in zip(xs, names, fills):
        label_box(s, xx, 2.2, bw, 0.55, n, f, RED if n == "廣告" else BORDER, 13, True, INK, lw=1.25, pad=0.03)
    for i in range(4):
        arrow(s, xs[i] + bw + 0.02, 2.47, xs[i + 1] - 0.02, 2.47, RED)
    conds = {0: ["2 種以上訊號", "有跑馬列的台 3 種"], 1: ["持續 25 秒以上"], 3: ["回新聞 3 項成立：", "版面恢復、語音、", "標題條出現新標題"]}
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
    text(s, 4.85, 4.4, 2.6, 2.5, ["主 bar 的標題穩定 7 秒，再加 1 項佐證才算新題：語音主題改變、記者名條或主播鏡頭。", "3 分鐘內同一標題重複出現，算原本那一則。", "廣告前不到 20 秒的片段併進廣告。"], 12, False, INK, space=6)
    # ad frames
    fx = [7.75, 10.25]
    fy = [1.85, 3.5]
    frames = ["ettv_1234_0_ad05.jpg", "ettv_1234_0_ad35.jpg", "ettv_1234_0_ad65.jpg", "ettv_1234_0_ad95.jpg"]
    for i, f in enumerate(frames):
        pic(s, f, fx[i % 2], fy[i // 2], w=2.4)
        text(s, fx[i % 2], fy[i // 2] + 1.38, 2.4, 0.22, ["12:34:57", "12:36:09", "12:37:20", "12:38:32"][i], 11, True, INK)
    text(s, 7.75, 5.1, 4.9, 0.25, "同一段廣告破口：12:34:46–12:38:44，頭尾平均截 4 張", 11, False, MUTE)
    box(s, 7.75, 5.55, 4.9, 1.05, SOFTRED, None, 0.08)
    text(s, 7.95, 5.63, 4.5, 0.9, ["包框期間凍結 bar 的文字，殘留的標題不採用；回新聞後重新起標。", "廣告起始那一分鐘填「廣告」。"], 12, False, INK, space=3)
    return s



def s_tech(prs):
    s = blank(prs)
    frame(s, "技術與驗證", "技術與驗證", 7, "11/06 12:00–13:00 四台共 90 格；準確度對照 TVBS 研究者單人初標（一台、約 30 個點，非正式雙人標註）。OCR：Apple Vision；語音：faster-whisper；LLM：qwen2.5:7b-instruct（本機，溫度 0）。")
    tag(s, 10.4, 0.6, 1.5, "回答 Q2、Q3、Q4")
    labs = [("標準答案（研究者初標）", "TVBS 一台，約 30 個點"), ("對照規則", "換題起始分鐘容許 ±1 分鐘"), ("調整與測試", "前 20 分鐘調整，12:20 後測試"), ("結果", "27 個換題點找到 23 個；廣告 10 分鐘全找到")]
    w, g = 2.75, 0.33
    for i, (a, b) in enumerate(labs):
        x = 0.67 + i * (w + g)
        label_box(s, x, 1.85, w, 0.9, [[(a, {"bold": True, "size": 13, "color": RED if i == 3 else INK})], [(b, {"size": 11, "color": MUTE})]], SOFTRED if i == 3 else CARD, None if i == 3 else BORDER, pad=0.08)
        if i < 3:
            arrow(s, x + w + 0.02, 2.3, x + w + g - 0.02, 2.3, RED, 1.25)
    text(s, 0.67, 2.95, 5.6, 0.28, "準確度對目標（TVBS 對照）", 13, True, RED)
    bar_chart_f1(s, 0.67, 3.2, 5.9, 2.55)
    text(s, 0.67, 5.85, 5.9, 1.0, [
        "自動填寫率 100%；四台一小時處理 46 分，目標 15 分，未達。",
        "未調整規則的 12:20 後測試段換題 F1 為 0.867；拿掉語音降到 0.758。", "F1：越接近 1 越準，同時看有沒有漏、有沒有多。"], 12, False, INK, space=3)
    text(s, 6.95, 2.95, 5.7, 0.28, "四台各自的結果（格）", 13, True, RED)
    # per channel, from out/12監看_20261106.report.json: (name, cells, story changes, ad breaks, low confidence)
    per = [("TVBS-N", 24, 25, 4, 9), ("東森", 27, 30, 4, 7), ("民視", 20, 18, 3, 6), ("華視", 19, 16, 4, 7)]
    cd = CategoryChartData()
    cd.categories = [f"{n}　換題 {c}・廣告 {a}" for n, _, c, a, _ in reversed(per)]
    cd.add_series("高信心", [t - l for _, t, _, _, l in reversed(per)])
    cd.add_series("低信心（列入抽查）", [l for _, _, _, _, l in reversed(per)])
    gf = s.shapes.add_chart(XL_CHART_TYPE.BAR_STACKED, Inches(6.95), Inches(3.2), Inches(5.7), Inches(2.3), cd)
    ch = gf.chart
    chart_text(ch, 11)
    ch.has_title = False
    ch.has_legend = True
    ch.legend.position = XL_LEGEND_POSITION.BOTTOM
    ch.legend.include_in_layout = False
    ch.legend.font.size = Pt(11)
    plot = ch.plots[0]
    plot.gap_width = 45
    plot.overlap = 100
    plot.has_data_labels = True
    plot.data_labels.position = XL_LABEL_POSITION.CENTER
    plot.data_labels.font.size = Pt(12)
    plot.data_labels.font.bold = True
    plot.data_labels.font.color.rgb = rgb("FFFFFF")
    for ser, c in zip(plot.series, ["A3A3A3", RED]):
        ser.format.fill.solid()
        ser.format.fill.fore_color.rgb = rgb(c)
    va = ch.value_axis
    va.minimum_scale, va.maximum_scale = 0, 30
    va.major_unit = 10
    va.has_major_gridlines = True
    va.major_gridlines.format.line.color.rgb = rgb(BORDER)
    va.format.line.fill.background()
    va.tick_labels.font.size = Pt(10)
    ch.category_axis.tick_labels.font.size = Pt(11)
    ch.category_axis.format.line.color.rgb = rgb(BORDER)
    text(s, 6.95, 5.52, 5.7, 0.5, "90 格全由系統填寫；低信心（系統沒把握）29 格（32%）照常填，另列抽查。", 11, True, RED)
    box(s, 6.95, 6.0, 5.7, 0.92, SOFTRED, None, 0.08)
    text(s, 7.1, 6.0, 5.4, 0.92, ["五字標的機器檢查（89 個）：字數 4–6 全部符合；每個字都在該段畫面標題裡，沒有憑空編的字。人工可用率還沒評分。", "重跑 240 格逐格相同。"], 11, False, INK, PP_ALIGN.LEFT, MSO_ANCHOR.MIDDLE, space=2)
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
    text(s, 7.4, 1.85, 5.2, 0.28, "怎麼算（假設值）", 14, True, RED)
    box(s, 7.4, 2.25, 5.25, 2.95, CARD, BORDER, 0.1)
    calc = [("每日監看", "18 小時"), ("× 365 天", "6,570 小時／年"), ("× 系統接手 85%", "5,585 小時／年"), ("× 時薪 NT$235", "131 萬"), ("− 維運成本", "22 萬"), ("＝ 淨效益", "109 萬／年")]
    y = 2.35
    for i, (a_, b_) in enumerate(calc):
        last = i == len(calc) - 1
        if last:
            box(s, 7.55, y - 0.02, 4.95, 0.44, SOFTRED, None, 0.08)
        text(s, 7.75, y, 2.5, 0.4, a_, 13, True, RED if last else INK, anchor=MSO_ANCHOR.MIDDLE)
        text(s, 10.0, y, 2.35, 0.4, b_, 14, True, ORANGE if i == 2 else (RED if last else INK), PP_ALIGN.RIGHT, MSO_ANCHOR.MIDDLE)
        y += 0.46
    text(s, 7.4, 5.4, 5.2, 0.28, "接手比例不同時（其餘假設不變）", 13, True, RED)
    for i, (r_, h_, n_) in enumerate([("70%", "4,599 小時", "86 萬"), ("85%", "5,585 小時", "109 萬"), ("95%", "6,242 小時", "125 萬")]):
        cx = 7.4 + i * 1.8
        mid = i == 1
        box(s, cx, 5.78, 1.65, 1.1, SOFTRED if mid else CARD, None if mid else BORDER, 0.08)
        text(s, cx, 5.84, 1.65, 0.3, "接手 " + r_, 12, True, RED if mid else INK, PP_ALIGN.CENTER)
        text(s, cx, 6.16, 1.65, 0.3, h_, 12, False, MUTE, PP_ALIGN.CENTER)
        text(s, cx, 6.46, 1.65, 0.32, "淨效益 " + n_, 13, True, INK, PP_ALIGN.CENTER)
    return s


def s_risk(prs):
    s = blank(prs)
    frame(s, "風險與保底", "風險與保底", 9, "低信心格另列抽查清單，由人確認；LLM 初判只用來排序，修正另存成修正版，原本的 Excel 不動。")
    tag(s, 11.15, 0.6, 0.8, "回答 Q5")
    text(s, 0.67, 1.85, 6.5, 0.28, "風險與對策", 14, True, RED)
    rows = [("標準答案只有單人初標", "補雙人標註，重算 F1 與一致性"), ("處理 46 分，目標 15 分", "換有 GPU 的機器實測"), ("15 格五字標由規則直接摘字", "列入抽查，由人確認"), ("廣告頭尾約差 10 秒", "午間並行比較時調規則"), ("畫面時鐘與檔名差 23–32 秒", "以畫面時鐘為準，待業師確認"), ("廣告後接回同一則怎麼記", "目前回新聞一律填標，待業師確認")]
    y = 2.2
    for a, b in rows:
        box(s, 0.67, y, 6.4, 0.68, CARD, BORDER, 0.08)
        text(s, 0.85, y + 0.04, 3.0, 0.6, a, 13, True, INK, anchor=MSO_ANCHOR.MIDDLE)
        arrow(s, 3.85, y + 0.34, 4.1, y + 0.34, RED, 1.25)
        text(s, 4.2, y + 0.04, 2.8, 0.6, b, 12, False, MUTE, anchor=MSO_ANCHOR.MIDDLE)
        y += 0.78
    text(s, 7.5, 1.85, 5.1, 0.28, "沒把握時的三條退路", 14, True, RED)
    outs = [("語音定名", "標題讀不到", "改用語音決定名稱，標低信心"), ("規則擷取", "LLM 寫的標兩次沒過檢查", "改用規則直接從標題摘字，標低信心"), ("換題存疑", "換題佐證不足", "照常填寫，標低信心")]
    y = 2.2
    for code, a_, b_ in outs:
        box(s, 7.5, y, 5.15, 0.85, CARD, BORDER, 0.08)
        label_box(s, 7.65, y + 0.2, 1.15, 0.45, code, SOFTRED, None, 13, True, RED, radius=0.1, pad=0.02)
        text(s, 8.98, y + 0.1, 3.6, 0.3, a_, 14, True, INK)
        text(s, 8.98, y + 0.45, 3.6, 0.3, b_, 12, False, MUTE)
        y += 0.97
    box(s, 7.5, 5.15, 5.15, 0.7, CARD, BORDER, 0.08)
    text(s, 7.7, 5.15, 4.8, 0.7, [[("拿掉語音，", {}), ("換題 F1 由 0.885 降到 0.758", {"bold": True, "color": RED}), ("，仍照常出表。", {})]], 13, False, INK, anchor=MSO_ANCHOR.MIDDLE)
    box(s, 7.5, 6.0, 5.15, 0.92, SOFTRED, None, 0.08)
    text(s, 7.7, 6.0, 4.8, 0.92, "表上不寫「待確認」：題目要求日常產製自動完成、不新增記法，低信心只記在依據與抽查清單，行政貼收視率時不會多出一種要人工處理的格。", 11, False, INK, anchor=MSO_ANCHOR.MIDDLE)
    return s


def s_deploy(prs):
    s = blank(prs)
    frame(s, "上線與擴大", "上線：接在側錄系統後面", 10, "上線門檻與時程是提案設定，待業師確認。處理時間為 Mac M4 快速模式實測。")
    tag(s, 10.4, 0.6, 1.5, "回答 Q1、Q5")
    w, g = 2.7, 0.4
    xs = [0.67 + i * (w + g) for i in range(4)]
    top = [("三立內部側錄系統", "既有系統，不取代"), ("待處理清單", "每小時切檔，自動排入"), ("NewsBar 處理流程", "地端 GPU 伺服器，四台並行"), ("監看表 xlsx", "交給監看與分析")]
    for i, (a_, b_) in enumerate(top):
        label_box(s, xs[i], 1.95, w, 0.95, [[(a_, {"bold": True, "size": 14, "color": "FFFFFF" if i == 2 else INK})], [(b_, {"size": 11, "color": "FFFFFF" if i == 2 else MUTE})]], RED if i == 2 else CARD, RED if i == 2 else BORDER)
        if i < 3:
            arrow(s, xs[i] + w + 0.02, 2.42, xs[i + 1] - 0.02, 2.42, RED)
    arrow(s, xs[2] + w / 2, 2.93, xs[2] + w / 2, 3.38, RED)
    label_box(s, xs[2], 3.4, w, 0.95, [[("每日品質日報", {"bold": True, "size": 14})], [("填寫率、低信心與讀不到標題比例", {"size": 11, "color": MUTE})]], CARD, BORDER)
    arrow(s, xs[2] + w + 0.02, 3.87, xs[3] - 0.02, 3.87, RED)
    label_box(s, xs[3], 3.4, w, 0.95, [[("監看員抽查", {"bold": True, "size": 14})], [("只看低信心格與抽樣", {"size": 11, "color": MUTE})]], CARD, BORDER)
    arrow(s, xs[3] + w / 2, 4.38, xs[3] + w / 2, 4.7, ORANGE)
    label_box(s, xs[2], 4.72, 2 * w + g, 0.55, "修正紀錄 → 每週更新規則與提示詞 → 回到處理流程（有版本號，可退回）", SOFTOR, None, 12, True, INK)
    text(s, 0.67, 3.4, 5.9, 0.28, "處理時間花在哪（Mac M4，快速模式實測）", 14, True, RED)
    bx, bw = 0.67, 5.9
    box(s, bx, 3.85, bw * 43.5 / 46.2, 0.4, ORANGE, None, 0)
    box(s, bx + bw * 43.5 / 46.2, 3.85, bw * 2.7 / 46.2, 0.4, RED, None, 0)
    text(s, bx + 2.2, 3.85, 3.4, 0.4, "抽取（讀標題、語音轉錄等）43.5 分", 12, True, "FFFFFF", anchor=MSO_ANCHOR.MIDDLE)
    text(s, bx + bw - 1.3, 4.3, 1.3, 0.25, "判斷 2.7 分", 11, True, RED, PP_ALIGN.RIGHT)
    box(s, bx + bw * 15 / 46.2 - 0.015, 3.78, 0.03, 0.54, INK, None, 0)
    text(s, bx + bw * 15 / 46.2 - 0.6, 4.35, 1.2, 0.22, "目標 15 分", 10, True, INK, PP_ALIGN.CENTER)
    text(s, 0.67, 4.72, 5.9, 0.7, "語音轉錄一次只能跑一台，是主要瓶頸；判斷本身只要 2.7 分鐘。四台並行（GPU）有機會壓到 15 分鐘，尚待實測。", 12, False, INK)
    box(s, 0.67, 5.5, 5.9, 1.42, CARD, BORDER, 0.08)
    text(s, 0.85, 5.58, 5.5, 0.28, "目前進度", 13, True, RED)
    text(s, 0.85, 5.9, 5.55, 1.0, [[("已實作：", {"bold": True}), ("全天自動處理、每日品質日報、人工複查與修正版", {})], [("設計中：", {"bold": True}), ("地端 GPU 伺服器部署、規則版本回退", {})]], 12, False, INK, space=5)
    box(s, 6.8, 5.5, 5.85, 1.42, SOFTRED, None, 0.08)
    text(s, 7.0, 5.58, 5.5, 0.28, "擴大全天還缺", 13, True, RED)
    text(s, 7.0, 5.9, 5.5, 1.0, ["正式雙人標註，重算 F1 與標註一致性。", "有 GPU 的機器實測，目標四台一小時 15 分鐘。", "午間並行比較：F1 ≥ 0.85、人工 ≤ 3 小時／日。", "業師確認：廣告後怎麼記、節目預告、最晚交表時間。"], 12, False, INK, space=2)
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
