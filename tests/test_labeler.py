"""spec §6 — T13–T15 and the §6.4 examples."""

import pytest
from conftest import FakeGenerator

from newsbar.labeler import LabelSource, label_length, make_label, rule_label, validate

TUCHENG = LabelSource(["新北土城一七旬老翁 轉彎不慎遭貨車輾亡"])
GAO = LabelSource(["張俊傑「9分鐘偷渡術」曝 高金素梅共諜案起訴求刑14年"])


@pytest.mark.parametrize("label,src", [("土城翁輾亡", TUCHENG), ("俊傑偷渡中", GAO), ("高金遭起訴", GAO)])
def test_task_examples_accepted(label, src):
    assert validate(label, src) == []


def test_T13_wrong_place_rejected():
    assert {"SRC-2", "LBL-4"} <= set(validate("板橋翁輾亡", TUCHENG))


def test_social_without_place_rejected():
    assert "LBL-4" in validate("老翁遭輾亡", TUCHENG)


def test_T14_unconfirmed_as_fact_rejected():
    assert "SRC-2" in validate("高金被判刑", GAO)  # 起訴求刑 ≠ 判刑


@pytest.mark.parametrize("bad", ["土城", "土城翁轉彎遭輾亡", "廣告", "土城/輾亡"])
def test_length_and_grammar(bad):
    assert validate(bad, TUCHENG) != []


def test_label_length_counts_ascii_words_as_one():
    assert label_length("MUSIC EXPO") == 2
    assert label_length("Youbike椅太高") == 4


def test_retry_then_accept():
    gen = FakeGenerator("板橋翁輾亡", "土城翁輾亡")
    res = make_label(TUCHENG, gen)
    assert res.label == "土城翁輾亡" and res.attempts == 2 and res.fallback is None


def test_T15_two_failures_go_to_rule_fallback():
    gen = FakeGenerator("板橋翁輾亡", "老翁被撞死")
    res = make_label(TUCHENG, gen)
    assert res.fallback == "FB-4"
    assert res.label == "土城輾亡"
    assert validate(res.label, TUCHENG) == []


def test_rule_label_always_passes_validation():
    for src in (TUCHENG, GAO, LabelSource(["台股二度補跌 終場收高"]), LabelSource([], "立法院今天三讀通過預算案")):
        lbl = rule_label(src)
        assert validate(lbl, src) == [], (lbl, validate(lbl, src))


def test_no_generator_uses_rule_fallback():
    assert make_label(GAO, None).fallback == "FB-4"


def test_place_only_in_speech_is_not_demanded():
    src = LabelSource(["男二度毒駕拒檢催油門狂逃下秒遭車撞飛"], "高雄前鎮一名男子毒駕拒檢")
    assert validate("毒駕遭車撞", src) == []
    assert "SRC-2" in validate("前鎮毒駕逃", src)  # still may not borrow words from speech


def test_stock_slump_is_not_social_news():
    assert validate("台股崩3737", LabelSource(["史詩崩跌!台股殺3737點台積電聯發科一度跌停"])) == []
