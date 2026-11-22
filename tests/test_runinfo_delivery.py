"""RUN-1/RUN-2 版本紀錄與續跑檢查、QA-1/QA-2 品質狀態與交付檢查、EVAL-2 秒級評估、LBL-9/10、R20-2。"""

import json
from concurrent.futures import ThreadPoolExecutor

from newsbar import delivery, evaluate, llm, runinfo, words
from newsbar.evidence import AUTO_ACCEPTED, REVIEW_REQUIRED, quality_of
from newsbar.labeler import LabelRules, LabelSource, cut_words, make_label, orphan_units, repair, validate
from newsbar.schemas import Cell, Event, EventType


def test_RUN1_resume_only_when_inputs_match(tmp_path):
    video = tmp_path / "tvbs.ts"
    video.write_bytes(b"x" * 1000)
    (tmp_path / "tvbs").mkdir()
    (tmp_path / "tvbs" / "obs.jsonl").write_text("")
    meta = runinfo.extract_meta(video, {"roi": [0, 0, 1, 1]}, "vision", "none", None, None)
    ok, why = runinfo.resume_check(tmp_path, "tvbs", meta)
    assert ok and "沒有版本紀錄" in why  # old obs without meta: reused, with a warning
    runinfo.save_meta(tmp_path, "tvbs", meta)
    assert runinfo.resume_check(tmp_path, "tvbs", meta) == (True, None)
    changed = runinfo.extract_meta(video, {"roi": [0, 0, 1, 0.9]}, "vision", "none", None, None)
    ok, why = runinfo.resume_check(tmp_path, "tvbs", changed)
    assert not ok and "channel_cfg" in why
    video.write_bytes(b"y" * 1000)
    ok, why = runinfo.resume_check(tmp_path, "tvbs", runinfo.extract_meta(video, {"roi": [0, 0, 1, 1]}, "vision", "none", None, None))
    assert not ok and "video" in why


def test_RUN2_manifest_has_versions(tmp_path):
    tpl = tmp_path / "t.xlsx"
    tpl.write_bytes(b"t")
    m = runinfo.manifest("run_1", started="a", finished="b", inputs={"tvbs": {"size": 1}}, template=tpl,
                         channels_cfg={}, rules={"version": "3"}, lexicon_path=None, ocr_backend="vision", asr="none",
                         asr_model=None, llm_model="qwen", llm_state="on", resumed={"tvbs": "reused"}, out_files=[])
    assert m["run_id"] == "run_1" and m["rules_version"] == "3" and "commit" in m["code"]
    assert m["models"]["llm_model"] == "qwen" and m["extraction"] == {"tvbs": "reused"}
    json.dumps(m)


def _cell(ch, minute, value, fallback=None, conf="high"):
    ev = Event(ch, 0, EventType.NEWS if value not in ("廣告",) else EventType.AD,
               value if value != "廣告" else None, confidence=conf, fallback=fallback)
    return Cell(ch, minute, value, [ev])


def test_QA1_quality_status_is_data():
    assert quality_of({"fallback": "FB-4", "confidence": "low", "items": []})["status"] == REVIEW_REQUIRED
    q = quality_of({"fallback": None, "confidence": "high", "items": [{"boundary": {"review_required": False}}]})
    assert q["status"] == AUTO_ACCEPTED and q["reviewer"] is None and q["corrected_value"] is None


def test_QA2_delivery_check():
    cells = {"tvbs": {"1200": _cell("tvbs", "1200", "分局前縱火"), "1205": _cell("tvbs", "1205", "廣告")}}
    recs = [{"channel": "tvbs", "minute": m, "quality": {"status": AUTO_ACCEPTED}} for m in cells["tvbs"]]
    assert delivery.validate_delivery(cells, recs, ["tvbs"], "1200", 60)["status"] == delivery.READY
    recs[0]["quality"]["status"] = REVIEW_REQUIRED
    d = delivery.validate_delivery(cells, recs, ["tvbs"], "1200", 60)
    assert d["status"] == delivery.READY_WITH_REVIEW and d["review_required"] == 1
    cells["tvbs"]["1206"] = _cell("tvbs", "1206", "太長的標籤啊啊")
    d = delivery.validate_delivery(cells, recs, ["tvbs", "cts"], "1200", 60, failed={"cts": "boom"})
    assert d["status"] == delivery.INCOMPLETE
    assert any("cts" in p for p in d["problems"]) and any("1206" in p for p in d["problems"])


def test_EVAL2_seconds_report():
    gold = [{"channel": "tvbs", "sec": 0, "kind": "news"}, {"channel": "tvbs", "sec": 300, "kind": "news"},
            {"channel": "tvbs", "sec": 600, "kind": "ad"}, {"channel": "tvbs", "sec": 900, "kind": "news"}]
    stories = [{"channel": "tvbs", "start_sec": 5, "type": "news"}, {"channel": "tvbs", "start_sec": 450, "type": "news"},
               {"channel": "tvbs", "start_sec": 620, "type": "ad"}, {"channel": "tvbs", "start_sec": 900, "type": "news"}]
    r = evaluate.seconds_report(stories, gold, ["tvbs", "cts"], 1200)
    assert set(r) == {"tvbs"}  # cts has no gold: not scored
    t = r["tvbs"]
    assert t["boundary"]["tp"] == 2 and t["split"] == 1 and t["merge"] == 1
    assert t["ad_leak_sec"] == 20 and t["news_lost_to_ad_sec"] == 0  # 600–620 is ad in gold, news in the system


def test_LBL9_word_cuts():
    src = LabelSource(["臨停卸貨未警示.也未申請臨時證違規害婦撞亡"])
    assert "LBL-9" in validate("規害婦撞亡", src) and cut_words("規害婦撞亡", src) == ["違規"]
    assert "LBL-9" in validate("美股重挫元", LabelSource(["美股重挫元凶找到了德州新州長喊卡AI基建"]))
    assert validate("高金遭起訴", LabelSource(["張俊傑「9分鐘偷渡術」曝 高金素梅共諜案起訴求刑14年"])) == []
    assert orphan_units("工會串聯萬", LabelSource(["全國示威潮!工會串聯60萬人鐵路.觀光癱瘓"])) == ["60萬"]
    # one clean title of the story is enough
    assert cut_words("毒駕遭掏槍", LabelSource(["拒撿撞警車驗出毒駕警掏槍敲窗", "警車前後包夾!員警持槍包圍壓制毒駕男女"])) == []


def test_LBL10_repairs_near_misses_only_safely():
    src = LabelSource(["高雄老店歇業!海光俱樂部營運到今年底"])
    assert repair("海光俱樂部歇業", src, LabelRules()) == "海光俱樂部"
    assert repair("海光歇業", src, LabelRules()) is None
    src2 = LabelSource(["蔡阿嘎前員工蘿拉涉侵占逾2千萬遭起訴"])
    assert make_label(src2, type("G", (), {"generate": lambda self, s, a: "蔡阿嘎员工侵占"})()).label == "蔡阿嘎員工"


def test_words_thread_safe():
    texts = [f"台股盤中一度屠殺{i}點神山台積電拉回撐盤" for i in range(200)]
    with ThreadPoolExecutor(8) as ex:
        assert all(0 in b for b in ex.map(words.boundaries, texts))


def test_R20_2_breaker_reprobes_after_cooldown():
    now = [0.0]

    class Flaky:
        model = "m"
        ok = False

        def generate(self, s, a):
            if not self.ok:
                raise OSError("timeout")
            return "分局前縱火"

    inner = Flaky()
    g = llm.GuardedGenerator(inner, max_failures=2, cooldown_sec=30, clock=lambda: now[0])
    for _ in range(2):
        try:
            g.generate(LabelSource(["x"]), 0)
        except OSError:
            pass
    assert g.tripped
    inner.ok = True
    try:
        g.generate(LabelSource(["x"]), 0)
        raise AssertionError("should fail fast during cooldown")
    except llm.LLMError:
        pass
    now[0] = 31
    assert g.generate(LabelSource(["x"]), 0) == "分局前縱火" and not g.tripped


def test_R20_2_warm_up_reports_failure():
    class Gen:
        host, model = "http://127.0.0.1:1", "m"

        @staticmethod
        def opener(req, timeout):
            raise OSError("refused")
    assert llm.warm_up(Gen(), say=lambda *_: None) is False
    assert llm.warm_up(object()) is True  # not an Ollama generator: nothing to load


def test_EVAL3_hard_case_subsets():
    gold = [{"channel": "tvbs", "sec": 0, "kind": "news"},
            {"channel": "tvbs", "sec": 300, "kind": "news", "tags": ["短播報"]},
            {"channel": "tvbs", "sec": 330, "kind": "ad", "tags": ["廣告包框"]},
            {"channel": "tvbs", "sec": 400, "kind": "news", "tags": ["短播報"]}]
    stories = [{"channel": "tvbs", "start_sec": 0, "type": "news"}, {"channel": "tvbs", "start_sec": 300, "type": "news"},
               {"channel": "tvbs", "start_sec": 340, "type": "ad"}, {"channel": "tvbs", "start_sec": 400, "type": "news"}]
    h = evaluate.seconds_report(stories, gold, ["tvbs"], 600)["tvbs"]["hard_cases"]
    assert h["短播報"]["boundary_found"] == 2 and h["短播報"]["boundary_recall"] == 1.0
    assert h["廣告包框"]["ad_seconds"] == 70 and h["廣告包框"]["ad_leak_sec"] == 10


def test_QA1_review_verdicts_become_quality_status():
    from newsbar import review
    recs = [{"channel": "tvbs", "minute": m, "quality": {"status": REVIEW_REQUIRED}} for m in ("1200", "1201", "1202", "1203")]
    n = review.apply_review(recs, [
        {"channel": "tvbs", "minute": "1200", "verdict": "ok", "reviewer": "A", "reviewed_at": "t"},
        {"channel": "tvbs", "minute": "1201", "verdict": "fix", "correct": "台股崩跌", "reviewer": "A", "reviewed_at": "t"},
        {"channel": "tvbs", "minute": "1202", "verdict": "reject", "reviewer": "A", "reviewed_at": "t"}])
    assert n == {"approved": 1, "corrected": 1, "rejected": 1, "still_pending": 1}
    assert recs[1]["quality"]["status"] == "HUMAN_APPROVED" and recs[1]["quality"]["corrected_value"] == "台股崩跌"
    assert recs[2]["quality"]["status"] == "REJECTED" and recs[0]["quality"]["reviewer"] == "A"


def test_RUN3_failed_stage_is_not_reused(tmp_path):
    video = tmp_path / "v.ts"
    video.write_bytes(b"v")
    meta = runinfo.extract_meta(video, {}, None, "faster-whisper", None, None)
    (tmp_path / "tvbs").mkdir()
    (tmp_path / "tvbs" / "obs.jsonl").write_text("")
    runinfo.save_meta(tmp_path, "tvbs", meta)
    from newsbar.extract import save_stages
    save_stages(tmp_path / "tvbs", {"ocr": {"status": "SUCCEEDED"}, "asr": {"status": "FAILED"}})
    ok, why = runinfo.resume_check(tmp_path, "tvbs", meta)
    assert not ok and "asr" in why


def test_RUN3_asr_failure_is_none_not_empty(monkeypatch, tmp_path):
    from newsbar import asr as asrmod
    monkeypatch.setattr(asrmod, "extract_audio", lambda v, out, s, d: _wav(out))
    monkeypatch.setattr(asrmod, "transcribe_guarded", lambda b, w, sec: None)
    assert asrmod.attach([], object(), video=tmp_path / "v.ts") is None


def _wav(path):
    import wave
    with wave.open(str(path), "w") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b"\0\0" * 1600)
    return path


def test_RUN3_error_classes():
    import subprocess
    assert runinfo.classify_error(FileNotFoundError("x")) == "INPUT"
    assert runinfo.classify_error(subprocess.CalledProcessError(1, "ffmpeg")) == "INPUT"
    assert runinfo.classify_error(TimeoutError()) == "TRANSIENT"
    assert runinfo.classify_error(KeyError("x")) == "BUG"
