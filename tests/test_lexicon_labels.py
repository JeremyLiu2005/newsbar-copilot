"""Fixes 2, 4, 5: hedge check, overlay text filter, lexicon-driven places and rule condensation."""

from pathlib import Path

import yaml
from conftest import news, stream

from newsbar import lexicon, textfilter
from newsbar.labeler import LabelSource, make_label, rule_label, validate
from newsbar.pipeline import run_channel

ROOT = Path(__file__).resolve().parents[1]


# ---- fix 2 -----------------------------------------------------------------------------------
HEDGED = LabelSource(["某藝人疑似酒駕 警方調查中"])


def test_fix2_label_dropping_a_hedge_is_rejected():
    assert "TONE-1" in validate("某藝人酒駕", HEDGED)
    assert validate("藝人疑酒駕", HEDGED) == []
    assert "LBL-5" in validate("藝人疑似酒駕", HEDGED)  # 6 字：超過上限 5


def test_fix2_words_that_merely_contain_the_character_are_not_hedges():
    src = LabelSource(["政府加強宣傳 恐怖攻擊演練 民眾懷疑"])
    assert "TONE-1" not in validate("政府加強宣傳", src)


def test_fix2_retry_then_fallback_keeps_the_hedge():
    from conftest import FakeGenerator

    res = make_label(HEDGED, FakeGenerator("某藝人酒駕", "藝人酒駕中"))
    assert res.fallback == "FB-4" and lexicon.get().has_hedge(res.label) and "未確認語氣" not in res.reasons


def test_fix2_fallback_without_hedge_is_flagged():
    # a hedge marker the rule label cannot keep within 4–6 characters of copied source
    src = LabelSource(["某某某某某某某某某某恐"])
    res = make_label(src, None)
    assert "未確認語氣" in res.reasons


# ---- fix 4 -----------------------------------------------------------------------------------
def test_fix4_overlay_tokens_stripped_but_real_titles_kept():
    lex = lexicon.get()
    assert lex.strip_overlay("LIVE 新北土城老翁遭輾亡") == "新北土城老翁遭輾亡"
    assert lex.strip_overlay("新北土城老翁遭輾亡 LIVE直播") == "新北土城老翁遭輾亡"
    assert lex.strip_overlay("現場直播 台股崩") == "台股崩"
    assert lex.strip_overlay("Live") is None and lex.strip_overlay("28°C") is None and lex.strip_overlay("28度") is None
    assert lex.strip_overlay("最新民調出爐") == "最新民調出爐"  # not standalone
    assert lex.strip_overlay("今天最高28度") == "今天最高28度"


def test_fix4_live_flicker_does_not_change_the_story():
    # a LIVE tag appearing on the bar for 20 s must not create a second story
    obs = (news(60, 0, "台股二度補跌 終場收高", "t1") + news(20, 60, "LIVE 台股二度補跌 終場收高", "t1")
           + news(60, 80, "台股二度補跌 終場收高", "t1"))
    evs = run_channel("ettv", obs, None, {})
    assert len([e for e in evs if e.type.value == "news"]) == 1
    assert all("LIVE" not in (e.source.bar_text or "") for e in evs)


def test_fix4_filter_is_configurable_and_can_be_disabled(tmp_path):
    f = tmp_path / "lex.yaml"
    f.write_text("overlay_tokens: [快訊]\n", encoding="utf-8")
    lex = lexicon.load(f)
    assert lex.strip_overlay("快訊 台股崩") == "台股崩" and lex.strip_overlay("LIVE 台股崩") == "LIVE 台股崩"
    assert not textfilter.enabled({"text_filter": {"enabled": False}})
    obs = stream(3, bar_text="LIVE")
    assert textfilter.filter_obs(obs, lexicon.load())[0].bar_text is None and obs[0].bar_text == "LIVE"


# ---- fix 5 -----------------------------------------------------------------------------------
def test_fix5_no_hard_coded_place_list_left_in_labeler():
    src = (ROOT / "src/newsbar/labeler.py").read_text(encoding="utf-8")
    assert not any(w in src for w in ("PLACES", "板橋", "西門町", "鳳山", "SOCIAL_KEYWORDS ="))
    assert "WEATHER_KEYWORDS" not in (ROOT / "src/newsbar/segmenter.py").read_text(encoding="utf-8")


def test_fix5_lexicon_has_all_counties_and_townships_and_stays_in_sync():
    d = yaml.safe_load((ROOT / "config/lexicon.yaml").read_text(encoding="utf-8"))
    assert d == yaml.safe_load((ROOT / "src/newsbar/lexicon_default.yaml").read_text(encoding="utf-8"))
    assert sum(len(v) for v in d["districts"].values()) == 368
    lex = lexicon.get()
    for text, want in [("土城區", "土城"), ("北市大安", "台北"), ("新北市", "新北"), ("桃市", "桃園"), ("臺南市", "台南"),
                       ("三地門鄉", "三地門"), ("花蓮壽豐鄉", "壽豐"), ("連江縣", "連江"), ("金門縣", "金門")]:
        assert want in lex.places_in(text), text
    assert lex.places_in("警方成功逮捕嫌犯") == []  # 成功 alone is not 台東成功鎮
    assert lex.places_in("新北市") == ["新北"]  # 北市 inside 新北市 is not a second place


def test_fix5_place_list_is_editable_by_config(tmp_path):
    f = tmp_path / "lex.yaml"
    f.write_text("landmarks: [火星基地]\n", encoding="utf-8")
    assert lexicon.load(f).places_in("火星基地起火") == ["火星基地"]


def test_fix5_social_story_without_place_is_flagged():
    res = make_label(LabelSource(["大膽!分局前縱火燒警車男搭小黃逃逸"]), None)
    assert "社會新聞無地點" in res.reasons
    res2 = make_label(LabelSource(["新北土城一七旬老翁轉彎不慎遭貨車輾亡"]), None)
    assert "社會新聞無地點" not in res2.reasons


def test_fix5_rule_label_tucheng_keeps_the_place():
    src = LabelSource(["新北土城一七旬老翁轉彎不慎遭貨車輾亡"])
    lbl = rule_label(src)
    assert "土城" in lbl and validate(lbl, src) == []


def test_fix5_rule_label_drops_leading_shout():
    src = LabelSource(["大膽!分局前縱火燒警車男搭小黃逃逸"])
    lbl = rule_label(src)
    assert not lbl.startswith("大膽") and 4 <= len(lbl) <= 6 and validate(lbl, src) == []
    assert not rule_label(LabelSource(["史詩崩跌!台股殺3737點台積電聯發科一度跌停"])).startswith("史詩")
    assert rule_label(LabelSource(["台股二度補跌 終場收高"]))  # plain titles still work


def test_fix5_rule_label_is_copy_only_and_not_a_blind_cut():
    src = LabelSource(["台中市太平區男子酒駕撞護欄 闖紅燈遭攔查"])
    lbl = rule_label(src)
    assert "太平" in lbl and lbl != "台中市太平區"[:5] and validate(lbl, src) == []
