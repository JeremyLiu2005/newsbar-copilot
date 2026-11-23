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


# ---- OpenAI-compatible backend (NVIDIA hosted models) --------------------------------------------

def nv_backend(opener):
    b = vision.OpenAICompatVision(api_key="SECRET-KEY", model="some-vl-model")
    b.opener = staticmethod(opener)
    return b


def chat(content):
    return FakeResp(json.dumps({"choices": [{"message": {"content": content}}]}).encode())


def test_nvidia_request_shape_and_json_answer():
    seen = {}

    def opener(req, timeout):
        seen["req"] = req
        return chat('{"title": "台股殺3737點", "readable": true}')

    res = nv_backend(opener).read_title(b"\xff\xd8jpeg", "image/jpeg", "台股殺3737點")
    body = json.loads(seen["req"].data)
    assert (res.text, res.readable) == ("台股殺3737點", True)
    assert body["model"] == "some-vl-model" and body["temperature"] == 0 and body["max_tokens"] >= 1000
    parts = body["messages"][0]["content"]
    assert parts[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert "台股殺3737點" not in json.dumps(parts[0])  # the OCR text is not sent along
    assert seen["req"].get_header("Authorization") == "Bearer SECRET-KEY"


@pytest.mark.parametrize("answer,text,readable", [
    ('好的：{"title": "甲乙丙", "readable": true} 以上', "甲乙丙", True),
    ('{"title": "", "readable": false}', "", False),
    ("甲乙丙丁", "甲乙丙丁", True),  # a short plain answer is taken as the title
])
def test_parse_title_answer(answer, text, readable):
    r = vision.parse_title_answer(answer)
    assert (r.text, r.readable) == (text, readable)


def test_parse_rejects_long_free_text():
    with pytest.raises(vision.VisionError):
        vision.parse_title_answer("這是一段很長的說明" * 20)


@pytest.mark.parametrize("content", [None, "", "   "])
def test_nvidia_empty_answer_is_an_error(content):
    with pytest.raises(vision.VisionError, match="empty"):
        nv_backend(lambda req, timeout: chat(content)).read_title(b"x", "image/jpeg", "t")


def test_nvidia_http_error_has_no_key():
    def opener(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 429, "SECRET-KEY", {}, io.BytesIO(b"SECRET-KEY"))

    with pytest.raises(vision.VisionError) as e:
        nv_backend(opener).read_title(b"x", "image/jpeg", "t")
    assert str(e.value) == "HTTP 429"


def test_nvidia_from_env_needs_key_and_model():
    with pytest.raises(vision.VisionConfigError, match="金鑰"):
        vision.from_env("nvidia", env={})
    with pytest.raises(vision.VisionConfigError, match="模型"):
        vision.from_env("nvidia", env={"NVIDIA_API_KEY": "k"})
    b = vision.from_env("nvidia", env={"NVIDIA_API_KEY": "k", "NEWSBAR_VISION_MODEL": "m"})
    assert (b.api_key, b.model, b.url) == ("k", "m", vision.NVIDIA_URL)


def test_limit_samples_every_channel(tmp_path):
    wd = tmp_path / "work"
    for ch in ("tvbs", "ettv"):
        (wd / ch / "frames" / "main_bar").mkdir(parents=True)
        data = rows(f"{ch}標題標題標題", 9, conf=0.5)
        for r in data:
            (wd / ch / "frames" / "main_bar" / f"{r['sec']:05d}.jpg").write_bytes(b"\xff\xd8x")
        (wd / ch / "obs.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in data), encoding="utf-8")
    rep = vision.run_check(wd, ["tvbs", "ettv"], Spy(), send=True, limit=2, say=lambda s: None)
    assert sorted(r["channel"] for r in rep.rows) == ["ettv", "tvbs"]


# ---- which crop is sent ---------------------------------------------------------------------------

def test_pick_crop_where_ocr_itself_read_the_title():
    data = rows("住宅區槍響標題", 9, conf=0.5)
    raw = {r["sec"]: {"text": "", "conf": 0.0} for r in data}  # voting filled the title in…
    raw[2] = {"text": "住宅區槍響標題", "conf": 0.5}            # …but only second 2 really shows the bar
    raw[7] = {"text": "別的字", "conf": 1.0}
    run = vision.stable_runs("cts", data, raw=raw)[0]
    assert run.frame == "frames/main_bar/00002.jpg" and run.visible_secs == 2


def test_run_without_any_visible_bar_is_not_sent_and_counted(tmp_path):
    wd = make_workdir(tmp_path)
    (wd / "tvbs" / "ocr_raw.jsonl").write_text(
        "".join(json.dumps({"sec": s, "text": "", "conf": 0.0}) + "\n" for s in range(9)), encoding="utf-8")
    spy = Spy()
    rep = vision.run_check(wd, ["tvbs"], spy, send=True, say=lambda s: None)
    assert (rep.candidates, rep.no_bar, spy.calls) == (0, 1, 0)


def test_without_raw_reads_the_middle_crop_is_used():
    run = vision.stable_runs("tvbs", rows("甲乙丙丁戊", 9))[0]
    assert run.frame == "frames/main_bar/00004.jpg" and run.visible_secs is None


# ---- retry -----------------------------------------------------------------------------------------

class Flaky:
    def __init__(self, errors):
        self.errors, self.calls = list(errors), 0

    def read_title(self, image, media_type, ocr_text):
        self.calls += 1
        if self.errors:
            raise vision.VisionError(self.errors.pop(0))
        return vision.VisionResult(text=ocr_text)


@pytest.mark.parametrize("errors,calls,ok", [
    (["empty answer"], 2, True),       # empty answer: retried once, then fine
    (["HTTP 502"], 2, True),           # server error: retried once
    (["HTTP 502", "HTTP 502"], 2, False),
    (["HTTP 401"], 1, False),          # bad key: not retried
])
def test_retry_once_except_client_errors(errors, calls, ok):
    b = Flaky(errors)
    if ok:
        assert vision._read_with_retry(b, b"x", "標題").text == "標題"
    else:
        with pytest.raises(vision.VisionError):
            vision._read_with_retry(b, b"x", "標題")
    assert b.calls == calls


# ---- inside the labeling pipeline -----------------------------------------------------------------

from types import SimpleNamespace  # noqa: E402


class Fixed:
    def __init__(self, text, readable=True):
        self.text, self.readable, self.calls = text, readable, 0

    def read_title(self, image, media_type, ocr_text):
        self.calls += 1
        return vision.VisionResult(text=self.text, readable=self.readable)


def story_obs(tmp_path, title, conf=0.3, n=5):
    out = []
    for i in range(n):
        f = tmp_path / f"{i:05d}.jpg"
        f.write_bytes(b"\xff\xd8" + bytes([i]))
        out.append(SimpleNamespace(sec=i, bar_text=title, bar_conf=conf if i != 2 else conf + 0.1, frame=str(f)))
    return out


def test_reread_replaces_a_misread_title_that_keeps_most_characters(tmp_path):
    t = "數子提告沈伯洋訪談被移化接木這叫目首"
    b = Fixed("數字提告 沈伯洋:訪談被移花接木 這叫自首")
    titles, fixed, recs = vision.reread_titles([t], {t: 0.3}, story_obs(tmp_path, t), b)
    assert titles == ["數字提告 沈伯洋:訪談被移花接木 這叫自首"] and fixed == {0}
    assert recs[0]["status"] == "replaced" and recs[0]["frame"] == "00002.jpg"  # most confident second


@pytest.mark.parametrize("model,readable,status", [
    ("另一則完全不同的新聞標題", True, "kept_ocr"),  # a different headline is never brought in
    ("", False, "kept_ocr"),
])
def test_reread_keeps_ocr_when_the_model_does_not_confirm(tmp_path, model, readable, status):
    t = "住宅區槍響太歲頭上動土罪加一等送辦"
    titles, fixed, recs = vision.reread_titles([t], {t: 0.3}, story_obs(tmp_path, t), Fixed(model, readable))
    assert titles == [t] and fixed == set() and recs[0]["status"] == status


def test_reread_skips_confident_titles_and_errors_never_stop(tmp_path):
    t = "國安基金護盤戰績8勝1敗"
    b = Fixed("x")
    assert vision.reread_titles([t], {t: 1.0}, story_obs(tmp_path, t), b) == ([t], set(), [])
    assert b.calls == 0

    class Boom:
        def read_title(self, *a):
            raise vision.VisionError("HTTP 500")

    titles, fixed, recs = vision.reread_titles([t], {t: 0.3}, story_obs(tmp_path, t), Boom())
    assert titles == [t] and recs[0]["status"] == "error"


def test_cache_means_the_same_crop_is_not_sent_twice(tmp_path):
    t = "數子提告沈伯洋訪談被移化接木這叫目首"
    cache = vision.ReadCache(tmp_path / "cache.json")
    b = Fixed("數字提告沈伯洋訪談被移花接木這叫自首")
    obs = story_obs(tmp_path, t)
    vision.reread_titles([t], {t: 0.3}, obs, b, cache=cache)
    vision.reread_titles([t], {t: 0.3}, obs, b, cache=vision.ReadCache(tmp_path / "cache.json"))
    assert b.calls == 1


def test_off_by_default_in_the_pipeline():
    assert vision.backend_from_rules({}, env={}) is None
    assert vision.backend_from_rules({"vision": {"mode": "off"}}, env={}) is None
    assert isinstance(vision.backend_from_rules({}, env={"NEWSBAR_VISION_MODE": "mock"}), vision.MockVision)


def test_decide_story_uses_the_reread_title_and_notes_it(tmp_path):
    from newsbar import pipeline

    t = "數子提告沈伯洋訪談被移化接木這叫目首"
    d = pipeline.decide_story("ftv", [t], "", story_obs(tmp_path, t), None, None, {t: 0.3}, None,
                              Fixed("數字提告 沈伯洋:訪談被移花接木 這叫自首"))
    assert d["titles"][0].startswith("數字提告")
    assert any(n["reason"] == "標題經雲端模型重讀" for n in d["notes"])
    assert d["vision"][0]["status"] == "replaced"
    assert all("移化" not in (d["label"] or "") for _ in [0])


# ---- SAME-3b (segmenter.new_place) ----------------------------------------------------------------

@pytest.fixture
def project_lexicon():
    from newsbar import lexicon
    prev = lexicon._active
    lexicon.use("config/lexicon.yaml")
    yield
    lexicon._active = prev


def test_same3b_new_place_in_title_and_in_the_speech_before_it(project_lexicon):
    from newsbar.segmenter import new_place
    story = ["逃亡逾2月!漁港偷渡下車到上船僅5分鐘", "違反國安法偵結張俊傑再遭求刑15年"]
    assert new_place("斥資80億整修前鎮漁港爆梁柱鋼筋外露", story, ["高雄前鎮漁港斥資"])


def test_same3b_subtitle_adding_a_place_while_speech_continues_is_not_split(project_lexicon):
    from newsbar.segmenter import new_place
    story = ["武嶺2紅牌重機違停擋道找無車主警;最高3千罰鍰"]
    assert not new_place("找不到騎士.沒留電話還要開回台南上班", story, ["他們凌晨四點多", "不只找不到騎士"])


def test_same3b_shared_place_or_no_place_is_not_split(project_lexicon):
    from newsbar.segmenter import new_place
    assert not new_place("前鎮漁港梁柱鋼筋外露", ["斥資80億整修前鎮漁港"], ["高雄前鎮漁港"])
    assert not new_place("鋼筋外露攤商營運難施工", ["斥資80億整修前鎮漁港"], ["高雄市議員"])


# ---- R20-4: a channel's LLM-down fallbacks are asked again once ----------------------------------

def test_llm_down_fallbacks_are_retried_after_the_channel(tmp_path):
    from newsbar import llm, pipeline
    from conftest import news, stream

    class FlakyLLM:
        def __init__(self):
            self.calls = 0

        def generate(self, source, attempt):
            self.calls += 1
            if self.calls <= 2:
                raise RuntimeError("model still loading")
            return source.bar_texts[0][:5]

    gen = llm.GuardedGenerator(FlakyLLM(), max_failures=2, cooldown_sec=3600)
    obs = (news(60, 0, "土城翁輾亡七旬老翁", "a") + news(60, 60, "高金素梅遭起訴求刑", "b")
           + news(60, 120, "台積電法說會登場", "c"))
    evs = pipeline.run_channel("tvbs", obs, gen, {"labeler": {"parallel": 1}})
    news_evs = [e for e in evs if e.type.value == "news"]
    assert len({e.story_id for e in news_evs}) >= 2
    down = [e for e in news_evs if any(n.get("reason") == "AI 服務無回應" for n in e.notes)]
    assert not down, [(e.label, e.notes) for e in news_evs]


# ---- OCR-V3: 拒撿 (扌/木 misread of 拒檢) is a known confusion the short title must avoid -----------

def test_known_confusion_ju_jian_is_avoided():
    from newsbar import ocr_check
    titles, recs, avoid = ocr_check.check_story(["拒撿撞警車驗出毒駕警掏槍敲窗包圍壓制"], [], "")
    assert recs[0]["review_status"] == ocr_check.STATUS_PENDING
    assert "拒撿" in avoid and "撿" in avoid


# ---- SEM-1: a number cut at its decimal point is a fragment (涉收中共1 ← 1.7億) -----------------------

def test_number_cut_at_decimal_point_is_a_fragment():
    from newsbar import labeler
    src = labeler.LabelSource(["涉收中共1.7億!高金素梅遭求虛14年重刑"], "", "")
    assert labeler.number_fragments("涉收中共1", src) == ["1…"]
    assert labeler.number_fragments("收中共1.7億", src) == []
    label, errs = labeler.fallback_label(src)
    assert not label.endswith("1"), label


# ---- reproducibility: the retry prompt lists the doubtful pieces in a fixed order ----------------------

def test_avoid_feedback_order_does_not_depend_on_set_order():
    from newsbar import labeler

    class Rec:
        def __init__(self):
            self.fb = []

        def generate(self, source, attempt):
            self.fb.append(source.feedback)
            return '{"short_title": "拒撿撞警車", "evidence": ["拒撿撞警車"], "issues": []}'

    src = labeler.LabelSource(["拒撿撞警車驗出毒駕警掏槍敲窗包圍壓制"], "")
    outs = set()
    for avoid in ({"拒撿", "拒", "撿"}, {"撿", "拒撿", "拒"}, {"拒", "撿", "拒撿"}):
        g = Rec()
        labeler.make_label(src, g, 1, labeler.LabelRules(), set(avoid))
        outs.add(g.fb[1])
    assert len(outs) == 1 and "「拒撿」、「拒」、「撿」" in outs.pop()


# ---- LBL-5 in the prompt follows the rules file (exact five) and the cache key follows the prompt --------

def test_exact_five_prompt_and_cache_key(tmp_path):
    from newsbar import llm
    from newsbar.labeler import LabelSource
    g46 = llm.from_rules({"labeler": {"generator": "ollama", "min_len": 4, "max_len": 6}})
    g55 = llm.from_rules({"labeler": {"generator": "ollama", "min_len": 5, "max_len": 5}})
    assert g46.system == llm.SYSTEM and "4–6" in g46.system
    assert "剛好 5 個字" in g55.system and "高金共諜案" in str(g55.few_shot)
    src = LabelSource(["台南幼童成群穿越"], "")
    k46 = llm.CachedGenerator(llm.GuardedGenerator(g46), tmp_path / "c.json").key(src, 0)
    k55 = llm.CachedGenerator(llm.GuardedGenerator(g55), tmp_path / "c.json").key(src, 0)
    assert k46 != k55 and k46.startswith(f"{g46.model}|{llm.PROMPT_VERSION}|0|")


# ---- LBL-10b: trim a too-long AI answer to a title window that cuts nothing ----------------------------

def test_exact_five_trims_answer_to_clean_title_window():
    from newsbar import labeler as L
    R = L.LabelRules(min_len=5, max_len=5, target_len=5)
    src = L.LabelSource(["藍委為兒虐數字提告沈伯洋訪談被移花接木這叫自首"], "")
    assert L.repair_length("藍委提告沈伯洋", src, R) == "提告沈伯洋"
    assert L.repair_length("C羅宣布引退", L.LabelSource(["交還7號球衣退出國家隊C羅宣布引退球迷哀傷"], ""), R) is None
    assert L.repair_length("桃園警開4槍", L.LabelSource(["敬酒引爆大亂鬥桃園警開4槍喝止衝突"], ""), R) is None


# ---- FB-4c: the AI picks one checked rule candidate when its own answers keep failing ----------------------

def test_ai_picks_from_checked_candidates_when_own_answers_fail():
    from newsbar import labeler as L
    R = L.LabelRules(min_len=5, max_len=5, target_len=5)
    src = L.LabelSource(["囂張!汐止分局旁汽油彈燒警車51歲嫌桃園遭逮"], "")

    class Stubborn:
        def __init__(self):
            self.menus = []

        def generate(self, source, attempt):
            if "只能從下列候選" in source.feedback:
                self.menus.append(source.feedback)
                pick = source.feedback.split("「")[1].split("」")[0]  # first offered candidate
                return '{"short_title": "%s", "evidence": [], "issues": []}' % pick
            return '{"short_title": "汽油彈燒警車", "evidence": ["汽油彈燒警車"], "issues": []}'  # 6 chars, always

    g = Stubborn()
    res = L.make_label(src, g, 2, R)
    assert g.menus and res.fallback == "FB-4c" and L.label_length(res.label) == 5
    assert not L.validate(res.label, src, R)
    assert any(n["reason"] == L.REASON_MENU for n in res.notes)


def test_menu_skips_spans_that_cut_a_word():
    from newsbar import labeler as L
    assert not L.clean_span("前深圳禁按", ["APEC峰會前深圳禁按喇叭網kuso馬路變花果山"])
    assert not L.clean_span("羅宣布引退", ["交還7號球衣退出國家隊C羅宣布引退球迷哀傷"])
    assert L.clean_span("涉嫌燒警車", ["囂張!汐止分局旁汽油彈燒警車51歲嫌桃園遭逮"])  # joined label, not one span


# ---- LBL-9: a compound split into two real words is not a cut; half words still are -----------------------

def test_cut_words_allows_compound_split_but_not_half_words():
    from newsbar import labeler as L
    src = lambda t: L.LabelSource([t], "")  # noqa: E731
    assert L.cut_words("滿瑞富涉案", src("滿瑞富6號7涉案人員拘提到案6人聲押禁見")) == []
    assert L.cut_words("APEC峰", src("APEC峰會前深圳禁按喇叭網kuso馬路變花果山"))
    assert L.cut_words("電拉回撐盤", src("台股盤中一度屠殺3700點神山台積電拉回撐盤"))
    assert L.cut_words("蔣萬安稱下", src("跟進沈伯洋請益陳健治蔣萬安稱下週邀陳健治到競總"))
