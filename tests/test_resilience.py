"""Fixes 1, 3, 7: nothing uncertain may stall the run; failures are flagged, never silent."""

import json
import urllib.error

import pytest
from conftest import CHANNEL_HEADERS, EchoGenerator, build_template, news
from openpyxl import load_workbook

from newsbar import cli, ingest, llm, pipeline, server
from newsbar.labeler import LabelSource, make_label


class DeadGenerator:
    def __init__(self, exc=None):
        self.calls = 0
        self.exc = exc or urllib.error.URLError("refused")

    def generate(self, source, attempt):
        self.calls += 1
        raise self.exc


SRC = LabelSource(["新北土城一七旬老翁 轉彎不慎遭貨車輾亡"])


def test_fix1_llm_failure_falls_back_and_is_flagged():
    gen = DeadGenerator()
    res = make_label(SRC, gen)
    assert res.fallback == "FB-4" and "土城" in res.label and "輾亡" in res.label
    assert "AI 服務無回應" in res.reasons
    assert gen.calls == 1  # no second call against a dead service


@pytest.mark.parametrize("exc", [TimeoutError("t"), ConnectionResetError("c"), json.JSONDecodeError("x", "", 0), KeyError("message")])
def test_fix1_any_llm_error_is_caught(exc):
    assert "AI 服務無回應" in make_label(SRC, DeadGenerator(exc)).reasons


def test_fix1_ollama_generator_wraps_transport_and_json_errors():
    def refuse(req, timeout):
        raise urllib.error.URLError("refused")

    g = llm.OllamaGenerator()
    g.opener = refuse
    with pytest.raises(llm.LLMError):
        g.generate(SRC, 0)

    class Bad:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"<html>502</html>"

    g.opener = lambda req, timeout: Bad()
    with pytest.raises(llm.LLMError):
        g.generate(SRC, 0)


def test_fix1_guarded_generator_stops_waiting_after_repeated_failures():
    inner = DeadGenerator()
    g = llm.GuardedGenerator(inner, max_failures=2)
    for _ in range(5):
        with pytest.raises(Exception):
            g.generate(SRC, 0)
    assert inner.calls == 2 and g.tripped


def test_fix1_pipeline_with_dead_llm_still_writes_sheet(template, tmp_path):
    streams = {"tvbs": news(300, 0, "新北土城七旬翁 遭貨車輾亡", "t1")}
    out = tmp_path / "o.xlsx"
    res = pipeline.run(streams, template, out, {"tvbs": "TVBS-N"}, DeadGenerator())
    assert out.exists() and res["cells"]["tvbs"]["1200"].value
    rec = res["evidence"][0]
    assert "AI 服務無回應" in rec["reasons"] and rec["confidence"] == "low"


def test_fix1_one_channel_exception_does_not_stop_the_others(template, tmp_path, monkeypatch):
    real = pipeline.run_channel

    def boom(channel, *a, **k):
        if channel == "ettv":
            raise RuntimeError("segmenter exploded")
        return real(channel, *a, **k)

    monkeypatch.setattr(pipeline, "run_channel", boom)
    streams = {"tvbs": news(300, 0, "台股二度補跌 終場收高", "t1"), "ettv": news(300, 0, "台股二度補跌 終場收高", "t1")}
    out = tmp_path / "o.xlsx"
    res = pipeline.run(streams, template, out, CHANNEL_HEADERS, EchoGenerator())
    assert "ettv" in res["failed"] and "segmenter exploded" in res["failed"]["ettv"]
    ws = load_workbook(out)["12監看"]
    assert any(c.value for row in ws.iter_rows() for c in row if c.value == res["cells"]["tvbs"]["1200"].value)
    # fix 7: the failed channel is in the review list, not in the workbook
    csv_text = out.with_suffix(".report.csv").read_text(encoding="utf-8-sig")
    assert "CHANNEL-FAILED" in csv_text and "ettv" in csv_text and "segmenter exploded" in csv_text


def test_fix1_all_channels_failing_is_a_clear_error(template, tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "run_channel", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    with pytest.raises(pipeline.PipelineError, match="沒有任何一台"):
        pipeline.run({"tvbs": news(60, 0, "台股", "t")}, template, tmp_path / "o.xlsx", CHANNEL_HEADERS)


def test_fix1_excel_failure_is_fatal_but_clear_and_keeps_evidence(tmp_path):
    tpl = build_template(tmp_path / "t.xlsx", prefill={(1200, "TVBS-N"): "舊資料"})
    out = tmp_path / "o.xlsx"
    with pytest.raises(pipeline.PipelineError, match="無法寫入 Excel.*不覆寫"):
        pipeline.run({"tvbs": news(300, 0, "台股二度補跌 終場收高", "t1")}, tpl, out, {"tvbs": "TVBS-N"}, EchoGenerator())
    assert out.with_suffix(".evidence.jsonl").exists() and not out.exists()


def test_fix7_failed_channel_in_review_html_and_server_result(template, tmp_path):
    from newsbar import review

    html_out = review.render([], {"tvbs": "TVBS-N"}, tmp_path / "r.html", failed={"東森": "RuntimeError: boom"})
    assert "整台失敗：東森" in html_out.read_text(encoding="utf-8")
    out = tmp_path / "x.xlsx"
    out.with_suffix(".report.json").write_text(json.dumps({"channels": {}, "failed_channels": {"ettv": "boom"}}), encoding="utf-8")
    assert server.read_result(out)["failed"] == {"ettv": "boom"}


# ---- fix 3 -----------------------------------------------------------------------------------
@pytest.mark.parametrize("name,expected", [
    ("tvbs_20261106_1200.ts", "12:00:00"), ("東森_20261106-120130.mp4", "12:01:30"),
    ("ftv 12-05.ts", "12:05:00"), ("cts.ts", None), ("tvbs_2026.ts", None)])
def test_fix3_clock_from_filename(name, expected):
    assert ingest.clock_from_filename(name) == expected


def test_fix3_unreadable_clock_falls_back_to_filename_and_flags(monkeypatch, tmp_path):
    import newsbar.clock as clk

    monkeypatch.setattr(clk, "start_clock", lambda *a, **k: None)
    logs = []
    info = cli.resolve_clock_info(tmp_path / "tvbs_20261106_1200.ts", {"clock": [0, 0, 1, 1]}, None, None, logs.append)
    assert info == {"source": "filename", "value": "12:00:00", "reason": "時鐘未讀到，改用檔名時間"}
    assert any("改用檔名時間" in m for m in logs)
    none = cli.resolve_clock_info(tmp_path / "cts.ts", {}, None, None, logs.append)
    assert none["reason"] == "時間未校正" and none["value"] is None
    assert cli.resolve_clock_info(tmp_path / "cts.ts", {}, "12:00:00", None)["reason"] is None


def test_fix3_clock_reason_flags_every_cell_of_the_channel(template, tmp_path):
    streams = {"tvbs": news(600, 0, "台股二度補跌 終場收高", "t1") + news(600, 600, "立法院三讀通過預算案", "t2"),
               "ettv": news(600, 0, "台股二度補跌 終場收高", "t1")}
    res = pipeline.run(streams, template, tmp_path / "o.xlsx", CHANNEL_HEADERS, EchoGenerator(),
                       channel_reasons={"tvbs": ["時鐘未讀到，改用檔名時間"]})
    tv = [r for r in res["evidence"] if r["channel"] == "tvbs"]
    assert tv and all("時鐘未讀到，改用檔名時間" in r["reasons"] and r["confidence"] == "low" for r in tv)
    assert all("時鐘未讀到，改用檔名時間" not in r["reasons"] for r in res["evidence"] if r["channel"] == "ettv")
