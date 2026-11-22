"""End to end on synthetic streams — T03 (system level), T23, FB-R1/R2."""

import json

from conftest import CHANNEL_HEADERS, EchoGenerator, framed_ad, news, stream
from openpyxl import load_workbook

from newsbar.pipeline import run, run_channel
from newsbar.schemas import EventType as T


def test_T03_system_rewrites_label_after_framed_ad():
    bar = "新北土城七旬翁 遭貨車輾亡"
    obs = (news(30 * 60 + 10, 0, bar, "t1")
           + framed_ad(3 * 60, 30 * 60 + 10, bar)
           + news(5 * 60, 33 * 60 + 10, bar, "t1"))
    evs = run_channel("cts", obs, EchoGenerator())
    kinds = [(e.type, int(e.start_sec // 60)) for e in evs]
    assert kinds == [(T.NEWS, 0), (T.AD, 30), (T.NEWS, 33)]
    assert evs[0].label == evs[2].label and evs[0].story_id == evs[2].story_id


def test_framed_ad_residual_text_never_becomes_a_story():
    bar = "新北土城七旬翁 遭貨車輾亡"
    obs = news(60, 0, bar, "t1") + framed_ad(120, 60, "高金素梅共諜案起訴 求刑14年") + news(60, 180, "台股二度補跌 終場收高", "t3")
    evs = run_channel("cts", obs, EchoGenerator())
    assert [e.type for e in evs] == [T.NEWS, T.AD, T.NEWS]
    assert all("高金" not in (e.label or "") for e in evs)


def test_T23_ocr_dead_for_the_hour_still_fills_every_decision(template, tmp_path):
    topics = [("t1", "土城一名老翁遭貨車輾斃 家屬悲痛"), ("t2", "高金素梅共諜案今天起訴 求刑十四年"),
              ("t3", "台股今天二度補跌 終場收高")]
    streams = {}
    for ch in CHANNEL_HEADERS:
        obs = []
        for k in range(60):
            t, asr = topics[(k // 20) % 3]
            obs += stream(60, k * 60, bar_text=None, topic=t, asr_text=asr)
        streams[ch] = obs
    out = tmp_path / "12監看_20261106.xlsx"
    result = run(streams, template, out, CHANNEL_HEADERS, generator=None)
    ws = load_workbook(out)["12監看"]
    for ch in CHANNEL_HEADERS:
        assert set(result["cells"][ch]) == {"1200", "1220", "1240"}
    recs = [json.loads(line) for line in out.with_suffix(".evidence.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(recs) == 12
    assert all(r["fallback"] and "FB-3" in r["fallback"] for r in recs)  # FB-R2
    assert out.with_suffix(".report.csv").exists()
    assert ws.max_row >= 60
    # EVD-1/EVD-2: one story record per segment, each with boundary evidence; FB-3 ones need review
    stories = [json.loads(line) for line in out.with_suffix(".stories.jsonl").read_text(encoding="utf-8").splitlines()]
    assert stories and all("boundary_probability" in r and "evidence" in r for r in stories)
    assert all(r["review_required"] for r in stories if r["type"] == "news")
    assert all(r["end_sec"] > r["start_sec"] for r in stories)


def test_pipeline_writes_sheet_and_evidence(template, tmp_path):
    streams = {
        "tvbs": news(600, 0, "新北土城七旬翁 遭貨車輾亡", "t1") + news(600, 600, "高金素梅共諜案起訴 求刑14年", "t2", anchor_shot=True),
        "ettv": news(1200, 0, "台股二度補跌 終場收高", "t3"),
        "ftv": news(300, 0, "各地天氣 北部降雨機率高", "w") + news(900, 300, "立法院三讀通過預算案", "t4"),
        "cts": news(60, 0, "新北土城七旬翁 遭貨車輾亡", "t1") + framed_ad(120, 60, "新北土城七旬翁 遭貨車輾亡") + news(1020, 180, "台股二度補跌 終場收高", "t3"),
    }
    out = tmp_path / "out.xlsx"
    res = run(streams, template, out, CHANNEL_HEADERS, generator=EchoGenerator(), rules={"version": "0.1"})
    v = {ch: {m: c.value for m, c in cells.items()} for ch, cells in res["cells"].items()}
    assert set(v["tvbs"]) == {"1200", "1210"}
    assert v["ftv"]["1200"] == "氣象"
    assert v["cts"]["1201"] == "廣告" and "1203" in v["cts"]
    recs = res["evidence"]
    assert recs[0]["rules_version"] == "0.1"
    assert all(r["items"] for r in recs)
