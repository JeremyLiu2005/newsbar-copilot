"""spec §6 — T13–T15 and the §6.4 examples."""

import pytest
from conftest import FakeGenerator

from newsbar.labeler import LabelRules, LabelSource, label_length, make_label, review_notes, rule_label, validate

TUCHENG = LabelSource(["新北土城一七旬老翁 轉彎不慎遭貨車輾亡"])
GAO = LabelSource(["張俊傑「9分鐘偷渡術」曝 高金素梅共諜案起訴求刑14年"])


@pytest.mark.parametrize("label,src", [("土城翁輾亡", TUCHENG), ("俊傑偷渡中", GAO), ("高金遭起訴", GAO)])
def test_task_examples_accepted(label, src):
    assert validate(label, src) == []


def test_T13_wrong_place_rejected():
    assert "SRC-2" in validate("板橋翁輾亡", TUCHENG)  # 11/22：地點不再退件（LBL-4 改提示），補造的地點仍因 SRC-2 退件


def test_social_without_place_rejected():
    assert "LBL-4" not in validate("老翁遭輾亡", TUCHENG)  # 11/22：缺地點只記資訊提示
    assert any(n["reason"] == "短標未含地點" and n["level"] == "資訊提示" for n in review_notes("老翁遭輾亡", TUCHENG))


def test_T14_unconfirmed_as_fact_rejected():
    assert "SRC-2" in validate("高金被判刑", GAO)  # 起訴求刑 ≠ 判刑


@pytest.mark.parametrize("bad", ["土城", "土城翁轉彎遭輾亡", "廣告", "土城/輾亡"])
def test_length_and_grammar(bad):
    assert validate(bad, TUCHENG) != []


def test_label_length_counts_every_visible_character():
    # 三立 11/22：五字標只能 5 個字 — 英數字逐字算，「泰山3100階」是 7 個字
    assert label_length("MUSIC EXPO") == 9
    assert label_length("Youbike椅太高") == 10
    assert label_length("泰山3100階") == 7
    assert label_length("土城車輾亡") == 5


def test_retry_then_accept():
    gen = FakeGenerator("板橋翁輾亡", "土城翁輾亡")
    res = make_label(TUCHENG, gen)
    assert res.label == "土城翁輾亡" and res.attempts == 2 and res.fallback is None


def test_T15_two_failures_go_to_rule_fallback():
    gen = FakeGenerator("板橋翁輾亡", "老翁被撞死")
    res = make_label(TUCHENG, gen)
    assert res.fallback == "FB-4"
    assert "土城" in res.label and "輾亡" in res.label  # 11/22：4–6 字、詞界式備援（不再固定 5 字）
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
    assert validate("台股殺跌停", LabelSource(["史詩崩跌!台股殺3737點台積電聯發科一度跌停"])) == []


# ---- 規則 LBL-4 / TONE-1 不過嚴、LBL-7 規則標籤不斷在字中間 -------------------------------------------
@pytest.mark.parametrize("title", [
    "卓榮泰信心喊話金管會下午宣布救市措施", "共和黨期中選舉慘敗!范斯切割川普歸咎美伊戰爭",
    "友邦巴拉圭總統潘尼亞捲入銀行倒閉辭職後遭彈劾", "柯志恩回防果貿掃街賴瑞隆搶攻新住民選票",
    "史詩崩跌台股殺3737點", "C羅宣布引退球迷哀傷", "張俊傑偷渡接應12分鐘曝光"])
def test_non_crime_stories_are_not_social_news(title):
    from newsbar.labeler import is_social
    assert not is_social(title)


@pytest.mark.parametrize("title", ["臨停卸貨未警示.也未申請臨時證違規害婦撞亡", "滿瑞富6號7涉案人員拘提到案6人聲押禁見", "滅火器爆炸", "男子持刀砍殺路人"])
def test_crime_stories_are_social_news(title):
    from newsbar.labeler import is_social
    assert is_social(title)


def test_source_without_place_keeps_good_label_and_flags():
    src = LabelSource(["男二度毒駕拒檢催油門狂逃下秒遭車撞飛"])
    res = make_label(src, FakeGenerator("毒駕遭車撞"))
    assert res.fallback is None and res.label == "毒駕遭車撞" and "短標未含地點" in res.reasons


def test_single_char_hedge_is_flagged_not_rejected():
    src = LabelSource(["蔣辦安心大遊行 傳出沈嗆他知大家不安"])
    assert "TONE-1" in validate("蔣辦大遊行", src)  # 傳出 is a two-character hedge
    src2 = LabelSource(["低溫恐探17度 日系品牌感謝祭"])
    assert validate("低溫探17", src2) == []  # a lone 恐 only flags
    res = make_label(src2, FakeGenerator("低溫探17"))
    assert res.fallback is None and "未確認語氣" in res.reasons


@pytest.mark.parametrize("title,bad", [
    ("臨停卸貨未警示.也未申請臨時證違規害婦撞亡", "臨停卸貨未"),
    ("滿瑞富6號7涉案人員拘提到案6人聲押禁見", "號7涉案人"),
    ("張俊傑偷瀆關鍵12分鐘中籍地下船長詭助逃10/28深夜疑先登錢春168號", "深夜疑先登"),
    ("買來防災險變火災!小型滅火器驚傳爆炸", "器驚傳爆炸"),
])
def test_rule_label_never_reproduces_broken_labels(title, bad):
    from newsbar.labeler import END_BAD
    src = LabelSource([title])
    lbl = rule_label(src)
    assert lbl != bad and lbl[-1] not in END_BAD and not lbl[0].isdigit() and 4 <= label_length(lbl) <= 6
    assert validate(lbl, src) == []


def test_rule_label_examples():
    assert rule_label(TUCHENG, LabelRules(min_len=5, max_len=5)) == "土城車輾亡"  # 剛好 5 字開關
    assert rule_label(LabelSource(["臨停卸貨未警示.也未申請臨時證違規害婦撞亡"]), LabelRules(min_len=4, max_len=6)) == "害婦撞亡"
    assert label_length(rule_label(LabelSource(["臨停卸貨未警示.也未申請臨時證違規害婦撞亡"]), LabelRules(min_len=5, max_len=5))) == 5  # 三立 11/22：剛好 5 字
    assert rule_label(LabelSource(["10天內3度同台交鋒江啟臣.何欣純同框並肩聊市政"]))[0] != "1"
    assert rule_label(LabelSource(["大膽!分局前縱火燒警車男搭小黃逃逸"]), LabelRules(min_len=5, max_len=5)) == "縱火燒警車"
