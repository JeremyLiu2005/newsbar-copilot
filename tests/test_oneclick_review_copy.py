"""Fixes 6, 8, 9: --input-dir, Ollama pre-flight, workbook verification, review copy."""

import json

import pytest
from conftest import CHANNEL_HEADERS, EchoGenerator, build_template, news
from openpyxl import load_workbook
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

from newsbar import cli, discover, excel_writer, llm, pipeline

CFG = {"tvbs": {"name": "TVBS-N"}, "ettv": {"name": "東森"}, "ftv": {"name": "民視"}, "cts": {"name": "華視"}}


# ---- fix 6 -----------------------------------------------------------------------------------
def test_fix6_run_help_lists_input_dir(capsys):
    with pytest.raises(SystemExit):
        cli.main(["run", "--help"])
    assert "--input-dir" in capsys.readouterr().out


def test_fix6_finds_videos_by_key_or_name_and_template(tmp_path):
    for n in ["tvbs_20261106_1200.ts", "東森午間.mp4", "ftv.ts", "CTS-1200.mov", "notes.txt", "範本.xlsx", "~$範本.xlsx"]:
        (tmp_path / n).write_bytes(b"x")
    found, warns = discover.find_videos(tmp_path, CFG)
    assert {k: v.name for k, v in found.items()} == {"tvbs": "tvbs_20261106_1200.ts", "ettv": "東森午間.mp4",
                                                     "ftv": "ftv.ts", "cts": "CTS-1200.mov"}
    assert discover.find_template(tmp_path)[0].name == "範本.xlsx"


def test_fix6_resolve_inputs_reports_missing_channel_as_failed(tmp_path):
    (tmp_path / "tvbs.ts").write_bytes(b"x")
    (tmp_path / "template.xlsx").write_bytes(b"x")
    a = cli.argparse.Namespace(video=None, input_dir=str(tmp_path), clock=None, template=None, out=None)
    specs, template, out, missing = cli._resolve_inputs(a, CFG)
    assert [c for c, _, _ in specs] == ["tvbs"] and template.name == "template.xlsx"
    assert set(missing) == {"ettv", "ftv", "cts"}


def test_fix6_ollama_already_up_or_started_or_unavailable():
    say = []
    assert llm.ensure_ollama("h", ping=lambda h: True, say=say.append)
    started = []
    state = {"n": 0}

    def ping(h):
        state["n"] += 1
        return bool(started) and state["n"] > 2

    assert llm.ensure_ollama("h", ping=ping, which=lambda n: "/bin/ollama", spawn=lambda cmd, **k: started.append(cmd),
                             sleep=lambda s: None, say=say.append)
    assert started == [["/bin/ollama", "serve"]]
    started.clear()
    assert not llm.ensure_ollama("h", ping=lambda h: False, which=lambda n: "/bin/ollama",
                                 spawn=lambda cmd, **k: started.append(cmd), sleep=lambda s: None, say=say.append)
    assert len(started) == 1 and any("規則擷取" in m for m in say)  # tried once, then warned
    assert not llm.ensure_ollama("h", ping=lambda h: False, which=lambda n: None, say=say.append)


# ---- fix 8 -----------------------------------------------------------------------------------
def _fancy_template(tmp_path):
    tpl = build_template(tmp_path / "fancy.xlsx")
    wb = load_workbook(tpl)
    ws = wb["12監看"]
    ws.conditional_formatting.add("C5:C60", CellIsRule(operator="greaterThan", formula=["0.5"], fill=PatternFill("solid", bgColor="FF0000")))
    dv = DataValidation(type="list", formula1='"a,b"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add("A1")
    ws.freeze_panes = "B5"
    wb.save(tpl)
    return tpl


def _cells():
    from newsbar.minute_mapper import map_events
    from newsbar.schemas import Event, EventType as T

    return map_events([Event("tvbs", 10, T.NEWS, "阿美麻糬亡")], CHANNEL_HEADERS.keys())


def test_fix8_cf_dv_freeze_and_rating_headers_survive(tmp_path):
    tpl = _fancy_template(tmp_path)
    out = tmp_path / "o.xlsx"
    excel_writer.write(tpl, out, _cells(), CHANNEL_HEADERS)
    ws = load_workbook(out)["12監看"]
    assert ws.freeze_panes == "B5" and len(ws.data_validations.dataValidation) == 1
    assert len(list(ws.conditional_formatting)) == 1
    assert [c.value for c in ws[4] if c.value in ("A30", "M30", "A4+")]


def test_fix8_loss_of_features_or_headers_is_detected():
    with pytest.raises(excel_writer.WriterError):
        excel_writer._check_same("凍結窗格", {"freeze_panes": "B5"}, {"freeze_panes": None})


def test_fix8_missing_rating_header_after_write_raises(tmp_path, monkeypatch):
    tpl = build_template(tmp_path / "t.xlsx")
    calls = []

    def fake(ws, row):
        calls.append(1)
        return {"X1": "A30"} if len(calls) == 1 else {}

    monkeypatch.setattr(excel_writer, "_rating_headers", fake)
    with pytest.raises(excel_writer.WriterError, match="A30/M30/A4"):
        excel_writer.write(tpl, tmp_path / "o.xlsx", _cells(), CHANNEL_HEADERS)


# ---- fix 9 -----------------------------------------------------------------------------------
def _flagged_run(tmp_path):
    tpl = build_template(tmp_path / "t.xlsx")
    streams = {"tvbs": news(300, 0, "新北土城七旬翁 遭貨車輾亡", "t1", asr="主播說明" * 30),
               "ettv": news(300, 0, "台股二度補跌 終場收高", "t1")}
    out = tmp_path / "o.xlsx"
    res = pipeline.run(streams, tpl, out, {"tvbs": "TVBS-N", "ettv": "東森", "ftv": "民視"}, EchoGenerator(),
                       channel_reasons={"tvbs": ["時鐘未讀到，改用檔名時間"]}, prior_failed={"ftv": "找不到影片"})
    return tpl, out, res


def test_fix9_review_copy_marks_flagged_cells_and_deliverable_is_clean(tmp_path):
    tpl, out, res = _flagged_run(tmp_path)
    rev = tmp_path / "o_複查版.xlsx"
    n = excel_writer.write_review_copy(out, rev, res["evidence"], {"tvbs": "TVBS-N", "ettv": "東森", "ftv": "民視"},
                                       {"ftv": "找不到影片"}, {"tvbs": 0})
    assert n >= 2
    ws, lay = load_workbook(rev)["12監看"], excel_writer.locate(load_workbook(rev)["12監看"], {"tvbs": "TVBS-N", "ettv": "東森", "ftv": "民視"})
    c = ws.cell(lay.minute_rows["1200"], lay.channel_cols["tvbs"])
    assert c.fill.fgColor.rgb.endswith("FFFF00") and c.comment
    text = c.comment.text
    assert "00:00:00" in text and "新北土城七旬翁" in text and "時鐘未讀到，改用檔名時間" in text
    assert len(text.split("語音：")[1].split("\n")[0]) <= 60
    f = ws.cell(lay.minute_rows["1200"], lay.channel_cols["ftv"])  # failed channel: first minute cell
    assert f.comment and "整台失敗" in f.comment.text and f.fill.fgColor.rgb.endswith("FFFF00")
    clean = load_workbook(out)["12監看"]
    assert all(x.comment is None for row in clean.iter_rows() for x in row)
    lay2 = excel_writer.locate(clean, {"tvbs": "TVBS-N", "ettv": "東森", "ftv": "民視"})
    targets = [clean.cell(r, col) for r in lay2.minute_rows.values() for col in lay2.channel_cols.values()]
    assert not any(x.fill.fill_type for x in targets)
    # the clean file still passes the before/after verification against its template
    ex = tmp_path / "again.xlsx"
    excel_writer.write(tpl, ex, res["cells"], {"tvbs": "TVBS-N", "ettv": "東森"})


def test_fix9_unflagged_cells_stay_plain(tmp_path):
    tpl, out, res = _flagged_run(tmp_path)
    rev = tmp_path / "r.xlsx"
    recs = [dict(r, fallback=None, confidence="high", reasons=[]) for r in res["evidence"]]
    excel_writer.write_review_copy(out, rev, recs, {"ettv": "東森"}, None, {})
    ws = load_workbook(rev)["12監看"]
    lay = excel_writer.locate(ws, {"ettv": "東森"})
    assert ws.cell(lay.minute_rows["1200"], lay.channel_cols["ettv"]).comment is None


def test_fix9_no_review_copy_flag_exists(capsys):
    with pytest.raises(SystemExit):
        cli.main(["run", "--help"])
    assert "--no-review-copy" in capsys.readouterr().out
