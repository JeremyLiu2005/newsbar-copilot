"""vision.py — optional cloud re-read of title crops. No test here touches the network."""

import io
import json
import urllib.error

import pytest

from newsbar import cli, vision


def rows(text, n, conf=0.5, start=0, frame=True, name="tvbs"):
    return [{"sec": start + i, "bar_text": text, "bar_conf": conf,
             "frame": f"frames/main_bar/{start + i:05d}.jpg" if frame else None} for i in range(n)]


# ---- what gets selected -------------------------------------------------------------------------

def test_stable_runs_ignore_short_jitter():
    data = rows("甲新聞標題", 10) + rows("乙", 3, start=10) + rows("丙新聞標題", 8, start=13)
    runs = vision.stable_runs("tvbs", data)
    assert [(r.text, r.seconds) for r in runs] == [("甲新聞標題", 10), ("丙新聞標題", 8)]


def test_select_runs_only_low_confidence_with_a_crop():
    data = rows("低信心標題", 9, conf=0.5) + rows("高信心標題", 9, conf=1.0, start=9) + rows("沒截圖標題", 9, conf=0.3, start=18, frame=False)
    chosen = vision.select_runs(vision.stable_runs("tvbs", data))
    assert [r.text for r in chosen] == ["低信心標題"]


def test_select_runs_lowest_confidence_first_and_limit():
    data = rows("甲甲甲甲", 8, conf=0.5) + rows("乙乙乙乙", 8, conf=0.3, start=8)
    chosen = vision.select_runs(vision.stable_runs("tvbs", data), limit=1)
    assert [r.text for r in chosen] == ["乙乙乙乙"]


# ---- agreement guard ------------------------------------------------------------------------------

@pytest.mark.parametrize("ocr,model,expected", [
    ("台股殺3737點", "台股殺3737點", "same"),
    ("促鍆餐券買1送1", "促銷餐券買1送1", "close"),
    ("完全不同的標題", "另一則新聞內容", "differs"),
    ("", "有字", "differs"),
])
def test_agreement(ocr, model, expected):
    assert vision.agreement(ocr, model)[0] == expected


# ---- configuration --------------------------------------------------------------------------------

def test_off_by_default_and_mock_needs_nothing():
    assert vision.from_env("off", env={}) is None
    assert isinstance(vision.from_env("mock", env={}), vision.MockVision)


def test_anthropic_needs_key_and_model_and_never_reads_files():
    with pytest.raises(vision.VisionConfigError, match="金鑰"):
        vision.from_env("anthropic", env={})
    with pytest.raises(vision.VisionConfigError, match="模型"):
        vision.from_env("anthropic", env={"NEWSBAR_VISION_API_KEY": "k"})
    b = vision.from_env("anthropic", env={"ANTHROPIC_API_KEY": "k", "NEWSBAR_VISION_MODEL": "m"})
    assert (b.api_key, b.model) == ("k", "m")


def test_unknown_mode():
    with pytest.raises(vision.VisionConfigError):
        vision.from_env("openai-ish", env={})


# ---- the HTTP call (fake opener) ------------------------------------------------------------------

class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def backend_with(opener):
    b = vision.AnthropicVision(api_key="SECRET-KEY", model="some-model")
    b.opener = staticmethod(opener)
    return b


def test_request_shape_forced_tool_and_image_only():
    seen = {}

    def opener(req, timeout):
        seen["req"], seen["timeout"] = req, timeout
        out = {"content": [{"type": "tool_use", "name": "report_title", "input": {"title": "台股殺3737點", "readable": True}}]}
        return FakeResp(json.dumps(out).encode())

    res = backend_with(opener).read_title(b"\xff\xd8jpeg", "image/jpeg", "台股殺3737點")
    body = json.loads(seen["req"].data)
    assert res.text == "台股殺3737點" and res.readable
    assert body["tool_choice"] == {"type": "tool", "name": "report_title"}
    assert body["model"] == "some-model" and seen["timeout"] == 30.0
    parts = body["messages"][0]["content"]
    assert parts[0]["type"] == "image" and parts[0]["source"]["media_type"] == "image/jpeg"
    assert "台股殺3737點" not in json.dumps(parts[1])  # the OCR text is not sent along: no anchoring
    assert seen["req"].get_header("X-api-key") == "SECRET-KEY"


@pytest.mark.parametrize("exc", [urllib.error.URLError("down"), TimeoutError("slow"), ConnectionResetError()])
def test_network_failures_become_visionerror_without_the_key(exc):
    def opener(req, timeout):
        raise exc

    with pytest.raises(vision.VisionError) as e:
        backend_with(opener).read_title(b"x", "image/jpeg", "t")
    assert "SECRET-KEY" not in str(e.value)


def test_http_error_message_has_no_body_or_key():
    def opener(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 401, "bad key SECRET-KEY", {}, io.BytesIO(b"SECRET-KEY"))

    with pytest.raises(vision.VisionError) as e:
        backend_with(opener).read_title(b"x", "image/jpeg", "t")
    assert str(e.value) == "HTTP 401"


def test_answer_without_tool_block_is_an_error():
    with pytest.raises(vision.VisionError):
        backend_with(lambda req, timeout: FakeResp(b'{"content": [{"type": "text", "text": "hi"}]}')).read_title(b"x", "image/jpeg", "t")


# ---- the check run ---------------------------------------------------------------------------------

def make_workdir(tmp_path):
    wd = tmp_path / "work"
    (wd / "tvbs" / "frames" / "main_bar").mkdir(parents=True)
    data = rows("低信心標題一二三", 9, conf=0.5)
    for r in data:
        (wd / "tvbs" / "frames" / "main_bar" / f"{r['sec']:05d}.jpg").write_bytes(b"\xff\xd8fake")
    (wd / "tvbs" / "obs.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in data), encoding="utf-8")
    return wd


class Spy:
    def __init__(self):
        self.calls = 0

    def read_title(self, image, media_type, ocr_text):
        self.calls += 1
        return vision.VisionResult(text=ocr_text, note="spy")


def test_dry_run_sends_nothing_even_with_a_backend(tmp_path):
    spy, said = Spy(), []
    rep = vision.run_check(make_workdir(tmp_path), ["tvbs"], spy, send=False, say=said.append)
    assert (rep.candidates, spy.calls, rep.sent) == (1, 0, 0) and rep.rows == []
    assert any("乾跑" in s for s in said)


def test_send_without_backend_is_still_a_dry_run(tmp_path):
    rep = vision.run_check(make_workdir(tmp_path), ["tvbs"], None, send=True, say=lambda s: None)
    assert rep.sent == 0


def test_send_with_backend_records_verdicts(tmp_path):
    spy = Spy()
    rep = vision.run_check(make_workdir(tmp_path), ["tvbs"], spy, send=True, say=lambda s: None)
    assert (spy.calls, rep.sent, rep.verdicts) == (1, 1, {"same": 1})
    assert rep.rows[0]["ocr"] == "低信心標題一二三"


def test_one_failed_call_does_not_stop_the_run(tmp_path):
    class Boom:
        def read_title(self, *a):
            raise vision.VisionError("HTTP 500")

    rep = vision.run_check(make_workdir(tmp_path), ["tvbs"], Boom(), send=True, say=lambda s: None)
    assert (rep.errors, rep.verdicts) == (1, {"error": 1})


def test_cli_dry_run_needs_no_key(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("NEWSBAR_VISION_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert cli.main(["vision-check", "--workdir", str(make_workdir(tmp_path)), "--channels", "tvbs"]) == 0
    assert "乾跑" in capsys.readouterr().out


def test_cli_send_without_key_stops_with_a_plain_message(tmp_path, monkeypatch):
    monkeypatch.delenv("NEWSBAR_VISION_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(SystemExit) as e:
        cli.main(["vision-check", "--workdir", str(make_workdir(tmp_path)), "--channels", "tvbs", "--send"])
    assert "金鑰" in str(e.value)


def test_cli_mock_send_writes_the_comparison_file(tmp_path, capsys):
    out = tmp_path / "v.jsonl"
    assert cli.main(["vision-check", "--workdir", str(make_workdir(tmp_path)), "--channels", "tvbs", "--mode", "mock",
                     "--send", "--out", str(out)]) == 0
    rec = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
    assert rec["verdict"] == "same" and rec["channel"] == "tvbs"
