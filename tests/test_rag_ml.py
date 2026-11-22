"""RAG (label KB) and ML (learned story-change / ad decisions). Both default OFF."""

import io
import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import framed_ad, news  # noqa: E402

from newsbar import kb as kbm  # noqa: E402
from newsbar import llm, mlmodel, server  # noqa: E402
from newsbar.ad_state import AdParams, detect  # noqa: E402
from newsbar.labeler import LabelSource  # noqa: E402
from newsbar.segmenter import CHANGE_FEATURES, SegParams, segment  # noqa: E402

GOOD = ("新北土城一七旬老翁 轉彎不慎遭貨車輾亡", "土城翁輾亡")


# ---------------------------------------------------------------- KB
def test_kb_filter_rejects_pairs_failing_validate(tmp_path):
    kb = kbm.KB()
    assert kb.upsert(kbm.Record("a", *GOOD, source="gold"))
    assert not kb.upsert(kbm.Record("b", "新北土城一七旬老翁 轉彎不慎遭貨車輾亡", "高雄翁輾亡"))  # 高雄 / 輾 not in title chars? 高 not in title
    assert not kb.upsert(kbm.Record("c", "疑似縱火 新北土城民宅火警", "土城民宅火"))  # hedge dropped (TONE-1)
    assert not kb.upsert(kbm.Record("d", "新北土城一七旬老翁 轉彎不慎遭貨車輾亡", "土城翁"))  # too short (LBL-5)
    assert [r.id for r in kb.records] == ["a"]


def test_build_filters_and_keeps_gold_title_lookup(tmp_path):
    work = tmp_path / "work" / "tvbs"
    work.mkdir(parents=True)
    rows = []
    for sec in range(0, 30):  # stable title 新北土城… from sec 0
        rows.append({"sec": sec, "bar_text": GOOD[0]})
    for sec in range(30, 60):
        rows.append({"sec": sec, "bar_text": "月薪5萬前導遊淪車手"})
    (work / "obs.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    gold = tmp_path / "gold.json"
    gold.write_text(json.dumps({"items": [
        {"channel": "tvbs", "sec": 2, "kind": "news", "label": GOOD[1]},
        {"channel": "tvbs", "sec": 31, "kind": "news", "label": "銀髮車手"},   # 銀髮 not in title → rejected
        {"channel": "tvbs", "sec": 500, "kind": "news", "label": "無標題題"},   # no title within ±10 s
        {"channel": "tvbs", "sec": 40, "kind": "ad", "label": ""}]}), encoding="utf-8")
    kb, st = kbm.build(gold, tmp_path / "work", tmp_path / "nope", seed=False)
    assert [r.title for r in kb.records] == [GOOD[0]]
    assert len(st["rejected"]) == 1 and any("no stable title" in n for n in st["notes"])


def test_corrections_pair_with_evidence_titles(tmp_path):
    d = tmp_path / "web" / "job1"
    d.mkdir(parents=True)
    ev = {"channel": "tvbs", "minute": "1200", "items": [
        {"type": "ad", "label": None, "bar_text": None},
        {"type": "news", "label": "舊標", "bar_text": GOOD[0]}]}
    (d / "x.evidence.jsonl").write_text(json.dumps(ev, ensure_ascii=False) + "\n", encoding="utf-8")
    (d / "corrections.json").write_text(json.dumps([
        {"channel": "tvbs", "minute": "1200", "verdict": "fix", "correct": "土城翁輾亡", "reviewed_at": "2026-11-20T00:00:00"}]),
        encoding="utf-8")
    kb, _ = kbm.build(None, tmp_path, tmp_path / "web", seed=False)
    assert [(r.title, r.label, r.source) for r in kb.records] == [(GOOD[0], "土城翁輾亡", "correction")]


def test_add_sheet_without_titles_cannot_pair(tmp_path):
    res = kbm.add_sheet(kbm.KB(), tmp_path / "s.xlsx", None, {}, "2026-01-01")
    assert res["added"] == 0 and "標題" in res["error"]


def test_retrieval_ranks_the_right_pair_and_leave_one_out():
    kb = kbm.KB([
        kbm.Record("1", "台中潭子男子酒駕撞護欄 闖紅燈遭攔查", "潭子酒駕撞"),
        kbm.Record("2", "新北土城一七旬老翁 轉彎不慎遭貨車輾亡", "土城翁輾亡"),
        kbm.Record("3", "高金素梅共諜案起訴 求刑14年", "高金遭起訴"),
    ])
    top = kb.retrieve("土城七旬翁遭貨車撞亡", k=2)
    assert top[0].id == "2"
    same = "新北土城一七旬老翁 轉彎不慎遭貨車輾亡"
    assert kb.retrieve(same, k=1)[0].id == "2"
    assert "2" not in [r.id for r in kb.retrieve(same, k=3, exclude_ids={"2"})]  # leave-one-out
    assert "2" not in [r.id for r in kb.retrieve(same, k=3, exclude_labels={"土城翁輾亡"})]
    assert kb.retrieve("zzzz", k=3) == []


# ---------------------------------------------------------------- prompt / cache
class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _capture(gen):
    seen = {}

    def opener(req, timeout):
        seen["body"] = json.loads(req.data)
        return _Resp(json.dumps({"message": {"content": "土城翁輾亡"}}).encode())

    gen.opener = opener
    return seen


SRC = LabelSource(["新北土城七旬翁 轉彎遭貨車輾亡"])


def _rag():
    return kbm.Rag(kbm.KB([kbm.Record("k1", "新北板橋老翁 騎車遭貨車撞亡", "板橋翁撞亡", source="gold")]), 3)


def test_rag_disabled_prompt_and_cache_key_are_the_old_ones(tmp_path):
    g = llm.OllamaGenerator()
    seen = _capture(g)
    g.generate(SRC, 0)
    legacy = [{"role": "system", "content": llm.SYSTEM}]
    for q, a in llm.FEW_SHOT:
        legacy += [{"role": "user", "content": f"主標題：{q}\n主播與記者語音節錄：（無）\n五字標："},
                   {"role": "assistant", "content": a}]
    legacy.append({"role": "user", "content": llm.build_user_prompt(SRC, 0)})
    assert json.dumps(seen["body"]["messages"], ensure_ascii=False) == json.dumps(legacy, ensure_ascii=False)
    c = llm.CachedGenerator(llm.GuardedGenerator(g), tmp_path / "c.json")
    assert c.key(SRC, 0) == f"{g.model}|{llm.PROMPT_VERSION}|0|{llm.build_user_prompt(SRC, 0)}"
    assert llm.from_rules({"labeler": {"generator": "ollama"}}).rag is None


def test_rag_prompt_carries_retrieved_pairs_and_changes_cache_key(tmp_path):
    g = llm.OllamaGenerator(rag=_rag())
    seen = _capture(g)
    g.generate(SRC, 0)
    msgs = seen["body"]["messages"]
    n = len(llm.FEW_SHOT)
    assert len(msgs) == 1 + 2 * n + 2 + 1
    assert "板橋老翁" in msgs[1 + 2 * n]["content"] and msgs[2 + 2 * n]["content"] == "板橋翁撞亡"
    off = llm.CachedGenerator(llm.OllamaGenerator(), tmp_path / "a.json").key(SRC, 0)
    on = llm.CachedGenerator(llm.GuardedGenerator(g), tmp_path / "b.json").key(SRC, 0)
    assert on != off and "v4-rag" in on and "k1" in on
    g2 = llm.OllamaGenerator(rag=kbm.Rag(kbm.KB(), 3))
    assert llm.CachedGenerator(g2, tmp_path / "c.json").key(SRC, 0) != on  # other retrieved set → other key


def test_rag_config_and_cli_kb_switch_it_on(tmp_path):
    p = tmp_path / "l.jsonl"
    kbm.KB([kbm.Record("k", *GOOD)]).save(p)
    assert llm.from_rules({"labeler": {"generator": "ollama", "rag": {"enabled": True, "kb": str(p), "k": 2}}}).rag.k == 2
    assert llm.from_rules({"labeler": {"generator": "ollama"}}, str(p)).rag.kb.records[0].id == "k"
    assert llm.from_rules({"labeler": {"generator": "ollama", "rag": {"enabled": False}}}).rag is None


def test_run_eval_loo_and_summary():
    items = [kbm.Record("g1", *GOOD, source="gold"), kbm.Record("g2", "台中潭子男子酒駕撞護欄", "潭子酒駕撞", source="gold")]
    kb = kbm.KB(items)

    class Gen:
        def __init__(self, rag, ids, labs):
            self.rag, self.ids, self.labs = rag, ids, labs

        def rag_pairs(self, src):
            return llm.retrieved_pairs(self.rag, src, self.ids, self.labs)

        def generate(self, src, attempt):
            return "土城翁輾亡" if "土城" in src.text else "潭子酒駕撞"

    rows = kbm.run_eval(items, kb, Gen, 3, "loo")
    assert all(g not in r.retrieved for r, g in zip(rows, ("g1", "g2")))
    assert kbm.summarize(rows) == {"n": 2, "char_f1": 1.0, "first_attempt_valid": 1.0, "fallback_to_rule": 0}


def test_server_correction_feeds_kb_and_failure_does_not_break_saving(tmp_path):
    out = tmp_path / "job"
    out.mkdir()
    ev = {"channel": "tvbs", "minute": "1200", "items": [{"type": "news", "label": "舊", "bar_text": GOOD[0]}]}
    (out / "12監看.evidence.jsonl").write_text(json.dumps(ev, ensure_ascii=False) + "\n", encoding="utf-8")
    job = server.Job("j1", [], out / "12監看.xlsx", videos={"tvbs": Path("x")})
    req = {"channel": "tvbs", "minute": "1200", "verdict": "fix", "correct": "土城翁輾亡",
           "error_type": "其他", "reviewer": "A"}
    kbp = tmp_path / "kb" / "labels.jsonl"
    server.save_correction(job, req, kbp)
    assert [(r.label, r.source) for r in kbm.KB.load(kbp).records] == [("土城翁輾亡", "correction")]
    server.save_correction(job, {**req, "verdict": "remove"}, kbp)
    assert kbm.KB.load(kbp).records == []
    rows = server.save_correction(job, req, tmp_path)  # a directory as KB path → feeding fails silently
    assert rows[0]["correct"] == "土城翁輾亡"


# ---------------------------------------------------------------- LR
def test_logreg_fits_separable_set():
    rng = np.random.default_rng(0)
    X = np.vstack([rng.normal(-2, 0.5, (30, 3)), rng.normal(2, 0.5, (30, 3))])
    y = np.array([0] * 30 + [1] * 30)
    rows = [{"features": {"a": x[0], "b": x[1], "c": x[2]}, "label": int(l)} for x, l in zip(X, y)]
    m = mlmodel.train_model("change", ["a", "b", "c"], rows, {"until": "x"})
    p = m.predict_vec(X)
    assert ((p >= m.threshold).astype(int) == y).all() and m.metrics["train"]["f1"] == 1.0
    with pytest.raises(ValueError):
        mlmodel.train_model("ad", ["a"], [{"features": {"a": 1.0}, "label": 0}] * 3, {})


def test_registry_versions_and_rollback(tmp_path):
    m = mlmodel.Model("change", ["a"], [1.0], 0.0, [0.0], [1.0], 0.5)
    p1 = mlmodel.save_model(m, tmp_path)
    p2 = mlmodel.save_model(m, tmp_path)
    assert (p1.name, p2.name) == ("change_v1.json", "change_v2.json")
    mlmodel.set_active("change", p1, tmp_path)
    mlmodel.set_active("change", p2, tmp_path)
    assert mlmodel.load_model("active", "change", tmp_path).version == 2
    assert mlmodel.rollback("change", tmp_path) == str(p1)
    assert mlmodel.load_model("active", "change", tmp_path).version == 1
    assert mlmodel.rollback("change", tmp_path) is None


# ---------------------------------------------------------------- learned mode
def _story_obs():
    obs = news(30, 0, "新北土城七旬翁 遭貨車輾亡", "t1")
    obs += news(30, 30, "台中潭子男子酒駕撞護欄 闖紅燈遭攔查", "t1")  # no NEW-2 evidence: rules keep it in the story (SAME-6)
    return obs


def _write(tmp_path, kind, feats, weights, bias, thr=0.5):
    m = mlmodel.Model(kind, feats, weights, bias, [0.0] * len(feats), [1.0] * len(feats), thr)
    return str(mlmodel.save_model(m, tmp_path))


def test_learned_change_mode_decides_by_probability(tmp_path):
    obs = _story_obs()
    assert len(segment(obs, detect(obs))) == 1  # rules
    yes = _write(tmp_path, "change", CHANGE_FEATURES, [0.0] * len(CHANGE_FEATURES), 5.0)
    no = _write(tmp_path, "change", CHANGE_FEATURES, [0.0] * len(CHANGE_FEATURES), -5.0)
    ad = detect(obs)
    segs = segment(obs, ad, SegParams(mode="learned", model=yes))
    assert [s.start for s in segs] == [0, 30] and "ML-new" in segs[1].signals
    segs = segment(obs, ad, SegParams(mode="learned", model=no))
    assert len(segs) == 1 and "ML-same" in segs[0].signals
    with pytest.raises(ValueError):
        segment(obs, ad, SegParams(mode="learned"))
    assert SegParams.from_rules({"segmenter": {"mode": "learned", "model": "m.json"}}).mode == "learned"


def test_recorder_collects_candidates_without_changing_rules_output():
    obs = _story_obs()
    rec: list = []
    segs = segment(obs, detect(obs), recorder=rec)
    assert len(segs) == 1 and len(rec) == 1
    assert set(rec[0]["features"]) == set(CHANGE_FEATURES) and rec[0]["rules_new"] is False


def test_learned_ad_mode_uses_probability_instead_of_signal_count(tmp_path):
    from newsbar.ad_state import AD_FEATURES

    obs = news(30, 0, "新北土城七旬翁 遭貨車輾亡", "t1") + framed_ad(40, 30, None) + news(30, 70, "高金素梅共諜案起訴", "t2")
    rules_breaks = [(b.start, b.end) for b in detect(obs).breaks]
    assert rules_breaks
    w = [0.0] * len(AD_FEATURES)
    w[AD_FEATURES.index("AD-a")] = 10.0  # only layout change counts
    m = _write(tmp_path, "ad", AD_FEATURES, w, -5.0)
    got = detect(obs, AdParams(mode="learned", model=m))
    assert [(b.start, b.end) for b in got.breaks] == rules_breaks
    never = _write(tmp_path, "ad", AD_FEATURES, [0.0] * len(AD_FEATURES), -9.0)
    assert detect(obs, AdParams(mode="learned", model=never)).breaks == []


# ---------------------------------------------------------------- rules regression
def test_rules_mode_output_is_unchanged_on_fixture():
    """Golden values come from the code before the ML refactor (main checkout)."""
    obs = news(30, 0, "新北土城七旬翁 遭貨車輾亡", "t1")
    obs += news(4, 30, "陳記者", "t1", speaker="reporter")
    obs += news(30, 34, "高金素梅共諜案起訴 求刑14年", "t2", anchor_shot=True)
    obs += news(12, 64, "共諜案偷渡術曝光 船長遭羈押", "t2")
    obs += news(20, 76, "台中潭子男子酒駕撞護欄 闖紅燈遭攔查", "t3")
    obs += framed_ad(40, 96, "新北土城七旬翁 遭貨車輾亡")
    obs += news(30, 136, "法國罷工巴黎鐵塔關閉", "t4")
    obs += news(30, 166, "法國罷工巴黎鐵塔關閉", "t4")
    ad = detect(obs)
    got = [(s.start, s.type.value, s.bar_texts, s.signals, s.confidence, s.fallback, s.reuse_of) for s in segment(obs, ad)]
    assert got == [
        (0, "news", ["新北土城七旬翁 遭貨車輾亡"], ["NEW-1"], "low", "FB-1", None),
        (34, "news", ["高金素梅共諜案起訴 求刑14年", "共諜案偷渡術曝光 船長遭羈押"],
         ["NEW-1", "NEW-2a", "NEW-2b", "NEW-2c", "NEW-2d"], "high", None, None),
        (76, "news", ["台中潭子男子酒駕撞護欄 闖紅燈遭攔查"], ["NEW-1", "NEW-2a"], "high", None, None),
        (96, "ad", [], ["AD-a", "AD-b", "AD-e"], "high", None, None)]
    assert sorted((b.start, b.end) for b in ad.breaks) == [(96, 136)]


def test_cli_registers_kb_and_ml_commands():
    from newsbar import cli

    for argv in (["kb", "build", "--help"], ["kb", "eval", "--help"], ["ml", "train", "--help"], ["ml", "rollback", "--help"]):
        with pytest.raises(SystemExit) as e:
            cli.main(argv)
        assert e.value.code == 0
