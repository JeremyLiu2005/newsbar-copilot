"""evaluate (FR-23), processing report (FR-30), review page (FR-15/16), cross-channel (FR-19)."""

import json

from conftest import CHANNEL_HEADERS, build_template

from newsbar.cross_channel import unify
from newsbar.evaluate import evaluate, processing_report, read_sheet, states
from newsbar.excel_writer import write
from newsbar.minute_mapper import map_events, minute_labels
from newsbar.review import apply_corrections, render
from newsbar.schemas import Event, EventType as T, Source

GOLD = {"tvbs": {"1200": "阿美麻糬亡", "1205": "談判砍男傷", "1210": "廣告", "1213": "追打害溺斃"}}


def test_perfect_match():
    r = evaluate(GOLD, GOLD)
    assert r.change["f1"] == 1.0 and r.ad["f1"] == 1.0 and r.errors == []


def test_tolerance_and_errors():
    sysd = {"tvbs": {"1201": "阿美麻糬亡", "1208": "多記一則", "1210": "廣告", "1214": "追打害溺斃"}}
    r = evaluate(sysd, GOLD)
    assert r.change["tp"] == 2 and r.change["fp"] == 1 and r.change["fn"] == 1
    types = sorted(e["type"] for e in r.errors)
    assert types == ["包框誤判", "漏換題", "過度換題"]
    assert r.ad["fp"] == 1  # system's ad runs to 1213; ad minutes get no tolerance


def test_ad_minutes_expand_blank_continuation():
    st = states(GOLD["tvbs"], minute_labels("1200", 15))
    assert [st[m] for m in ("1210", "1211", "1212", "1213")] == ["廣告", "廣告", "廣告", "追打害溺斃"]
    sysd = {"tvbs": {**GOLD["tvbs"], "1213": "廣告", "1214": "追打害溺斃"}}
    r = evaluate(sysd, GOLD)
    assert r.ad["fp"] == 1  # 1213 still ad in system, news in gold


def test_test_window_only():
    sysd = {"tvbs": {"1200": "完全錯誤", "1205": "談判砍男傷", "1210": "廣告", "1213": "追打害溺斃"}}
    assert evaluate(sysd, GOLD, only_minutes=("1205", "1259")).change["f1"] == 1.0


def test_read_sheet_round_trip(template, tmp_path):
    cells = map_events([Event("tvbs", 10, T.NEWS, "阿美麻糬亡"), Event("ftv", 600, T.AD)], CHANNEL_HEADERS)
    out = tmp_path / "o.xlsx"
    write(template, out, cells, CHANNEL_HEADERS)
    assert read_sheet(out, CHANNEL_HEADERS) == {"tvbs": {"1200": "阿美麻糬亡"}, "ettv": {}, "ftv": {"1210": "廣告"}, "cts": {}}


def ev(ch, t, label, bar):
    return Event(ch, t, T.NEWS, label, f"{ch}{t}", source=Source(bar))


def test_cross_channel_reuses_backed_label():
    a = ev("ettv", 100, "中逼康永歉", "中國施壓逼康永道歉 網友兩派論戰")
    b = ev("cts", 400, "康永遭施壓", "中國逼康永道歉 綜藝圈震撼")
    unify([a, b])
    assert b.label == "中逼康永歉"


def test_cross_channel_refuses_unbacked_label():
    a = ev("ettv", 100, "土城翁輾亡", "新北土城七旬翁 遭貨車輾亡")
    b = ev("cts", 200, "老翁遭輾斃", "七旬翁遭貨車輾斃 家屬悲痛")  # no 土城 in this channel's source
    unify([a, b])
    assert b.label == "老翁遭輾斃"


def test_cross_channel_respects_window():
    a = ev("ettv", 0, "中逼康永歉", "中國施壓逼康永道歉")
    b = ev("cts", 3000, "康永遭施壓", "中國逼康永道歉")
    unify([a, b], window_sec=900)
    assert b.label == "康永遭施壓"


EVID = [
    {"channel": "tvbs", "minute": "1200", "value": "阿美麻糬亡", "items": [{"type": "news", "frame": None, "bar_text": "x", "asr_text": None}],
     "signals": ["NEW-1"], "confidence": "low", "fallback": "FB-1", "rules_version": "0.1"},
    {"channel": "tvbs", "minute": "1210", "value": "廣告", "items": [{"type": "ad", "frame": None, "bar_text": None, "asr_text": None}],
     "signals": ["AD-a", "AD-b"], "confidence": "high", "fallback": None, "rules_version": "0.1"},
]


def test_processing_report_alerts_on_fallback_rate():
    rep = processing_report(EVID)
    d = rep["channels"]["tvbs"]
    assert d["changes"] == 1 and d["ads"] == 1 and d["fallback_rate"] == 0.5 and d["alert"]


def test_review_page_and_corrections(tmp_path):
    page = render(EVID, {"tvbs": "TVBS-N"}, tmp_path / "r.html").read_text(encoding="utf-8")
    assert "阿美麻糬亡" in page and "匯出修正紀錄" in page and "fallback" in page
    assert page.index("阿美麻糬亡") < page.index("<script>")
    fixed = apply_corrections({"tvbs": {"1200": "阿美麻糬亡"}},
                              [{"channel": "tvbs", "minute": "1200", "verdict": "fix", "correct": "追阿美麻糬"}])
    assert fixed["tvbs"]["1200"] == "追阿美麻糬"
