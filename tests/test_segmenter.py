"""spec §3, §4 — T08–T12."""

from conftest import framed_ad, news, stream

from newsbar.ad_state import detect
from newsbar.schemas import EventType as T
from newsbar.segmenter import segment


def segs_of(obs):
    return segment(obs, detect(obs))


def test_T08_live_tag_and_shot_cut_are_not_a_new_story():
    bar = "新北土城七旬翁 遭貨車輾亡"
    obs = news(30, 0, bar, "t1") + news(30, 30, bar, "t1", live_tag=True, shot_cut=True, speaker="reporter")
    segs = segs_of(obs)
    assert [s.type for s in segs] == [T.NEWS]


def test_T09_main_and_sub_title_rotation_is_same_story():
    a = "新北土城七旬翁 遭貨車輾亡"
    b = "土城翁轉彎不慎 家屬趕赴現場"
    obs = []
    for k in range(8):
        obs += news(8, k * 8, a if k % 2 == 0 else b, "t1")
    segs = segs_of(obs)
    assert len(segs) == 1
    assert set(segs[0].bar_texts) == {a, b}


def test_new_story_with_evidence_is_high_confidence():
    obs = news(30, 0, "新北土城七旬翁 遭貨車輾亡", "t1") + news(30, 30, "高金素梅共諜案起訴 求刑14年", "t2", anchor_shot=True)
    segs = segs_of(obs)
    assert [s.start for s in segs] == [0, 30]
    assert segs[1].confidence == "high" and "NEW-2a" in segs[1].signals


def test_FB1_without_asr_new_text_still_changes_with_low_confidence():
    obs = news(30, 0, "新北土城七旬翁 遭貨車輾亡", None, asr=None) + news(30, 30, "高金素梅共諜案起訴 求刑14年", None, asr=None)
    segs = segs_of(obs)
    assert len(segs) == 2
    assert segs[1].confidence == "low" and segs[1].fallback == "FB-1"


def test_SAME6_with_asr_and_no_evidence_title_rotation_stays_in_story():
    # real 11/06 TVBS: one story rotates 3 different titles while the speech topic stays the same
    obs = (news(12, 0, "敬酒引爆大亂鬥桃園警開4槍喝止衝突", "t1") + news(22, 12, "酒店內兩派人馬爆衝突驚動30名快打警", "t1")
           + news(40, 34, "60人圍觀喧嘩警鳴槍趕附近店家30年首見", "t1"))
    segs = segs_of(obs)
    assert len(segs) == 1 and "SAME-6" in segs[0].signals and len(segs[0].bar_texts) == 3


def test_subtitles_shorter_than_seven_seconds_are_ignored():
    obs = news(20, 0, "毒駕拒檢遭車撞飛男爬起還想掙脫警逃跑", "t1")
    for k, sub in enumerate(["你還跑你還跑", "你跑什麼你跑什麼", "打110打110"]):
        obs += news(3, 20 + 3 * k, sub, "t1")
    obs += news(20, 29, "毒駕拒檢遭車撞飛男爬起還想掙脫警逃跑", "t1")
    assert len(segs_of(obs)) == 1


def test_NEW2d_name_credit_marks_next_title_as_new_story():
    obs = (news(30, 0, "男二度毒駕拒檢催油門狂逃下秒遭車撞飛", "t1") + news(3, 30, "林佑璇趙立", "t1")
           + news(30, 33, "敬酒引爆大亂鬥桃園警開4槍喝止衝突", "t1"))  # same ASR topic id, but a credit just ended
    segs = segs_of(obs)
    assert [s.start for s in segs] == [0, 33] and "NEW-2d" in segs[1].signals


def test_TIME1_change_time_is_first_stable_second():
    obs = news(30, 0, "新北土城七旬翁 遭貨車輾亡", "t1") + news(1, 30, "高金素梅", "t2") + news(30, 31, "高金素梅共諜案起訴 求刑14年", "t2")
    segs = segs_of(obs)
    assert segs[-1].start == 31  # a 1-second flicker is ignored


def test_T10_ticker_changes_never_trigger():
    obs = [o for k in range(6) for o in news(10, k * 10, "新北土城七旬翁 遭貨車輾亡", "t1", ticker_text=f"跑馬燈第{k}則")]
    assert len(segs_of(obs)) == 1


def test_low_confidence_ocr_reads_are_ignored_when_asked():
    from newsbar.segmenter import SegParams
    obs = news(30, 0, "新北土城七旬翁 遭貨車輾亡", "t1") + news(30, 30, "高金素梅共諜案起訴", "t2", bar_conf=0.3)
    assert len(segment(obs, detect(obs), SegParams(min_conf=0.6))) == 1


def test_T11_weather_in_main_bar_is_weather():
    obs = news(30, 0, "新北土城七旬翁 遭貨車輾亡", "t1") + news(30, 30, "各地天氣 北部降雨機率高", "w")
    assert [s.type for s in segs_of(obs)] == [T.NEWS, T.WEATHER]


def test_T12_weather_corner_alone_is_not_weather():
    obs = news(60, 0, "新北土城七旬翁 遭貨車輾亡", "t1", weather_corner_text="台北 22°C 晴")
    assert [s.type for s in segs_of(obs)] == [T.NEWS]


def test_ocr_missing_falls_back_to_asr_topic():
    obs = stream(30, 0, bar_text=None, topic="t1", asr_text="土城一名老翁遭貨車輾斃") + \
        stream(30, 30, bar_text=None, topic="t2", asr_text="高金素梅共諜案今天起訴")
    segs = segs_of(obs)
    assert [s.start for s in segs] == [0, 30]
    assert all(s.fallback == "FB-3" for s in segs)


def test_headline_carousel_is_a_framed_ad_not_stories():
    """11/06 FTV: 10 teasers × 9 s looped twice while the picture played commercials."""
    titles = [f"預告標題第{k}則新聞內容" for k in range(7)]
    obs = news(40, 0, "新北土城七旬翁 遭貨車輾亡", "t1")
    t = 40
    for _ in range(2):
        for x in titles:
            obs += news(9, t, x, "t1")
            t += 9
    obs += news(40, t, "高金素梅共諜案起訴 求刑14年", "t2")
    segs = segs_of(obs)
    assert [s.type for s in segs] == [T.NEWS, T.AD, T.NEWS]
    assert segs[2].bar_texts[0].startswith("高金") and segs[2].start == t


def test_ad_copy_in_bar_plus_no_ticker_is_an_ad():
    from newsbar.schemas import is_ad_copy
    assert is_ad_copy("固力伸0800-030-031低至72折") and is_ad_copy("廣告後立即回到新聞現場")
    assert not is_ad_copy("涉收1.7億發展共諜組織遭起訴!檢求刑14年")
    obs = news(60, 0, "新北土城七旬翁 遭貨車輾亡", "t1", ticker_text="快訊", frame="f.jpg")
    obs += news(40, 60, "固力伸0800-030-031低至72折", "t9", ticker_text=None, frame="f.jpg")
    assert detect(obs).breaks and detect(obs).breaks[0].start == 60


def test_anchor_name_card_is_not_a_story():
    obs = news(30, 0, "新北土城七旬翁 遭貨車輾亡", "t1") + news(8, 30, "主播古彩彥", "t1") + news(40, 38, "史詩崩跌台股殺3737點台積電一度跌停", "t2")
    texts = [s.bar_texts[0] for s in segs_of(obs)]
    assert "主播古彩彥" not in texts and texts[-1].startswith("史詩崩跌")


def test_short_ad_copy_before_a_break_merges_into_the_ad():
    obs = news(60, 0, "新北土城七旬翁 遭貨車輾亡", "t1", ticker_text="快訊", frame="f")
    obs += news(9, 60, "watsog買一送一", "t9", ticker_text="快訊", frame="f")
    obs += news(60, 69, "固力伸0800-030-031低至72折", "t9", ticker_text=None, frame="f", clock_ok=False)
    obs += news(60, 129, "高金素梅共諜案起訴 求刑14年", "t2", ticker_text="快訊", frame="f")
    segs = segs_of(obs)
    assert [s.type for s in segs] == [T.NEWS, T.AD, T.NEWS]


def test_SAME8_returning_title_merges_the_split_story():
    a, b, c = "法國再陷入動亂各城罷工罷課巴黎鐵塔又關", "燒垃圾桶封路窩中生封校爆發警民推擠", "物價飛漲老百姓到月底根本入不敷出"
    obs = news(30, 0, a, "t1") + news(15, 30, b, "t2", anchor_shot=True) + news(15, 45, c, "t3", anchor_shot=True) + news(20, 60, a, "t4")
    segs = segs_of(obs)
    assert len(segs) == 1 and "SAME-8" in segs[0].signals and set(segs[0].bar_texts) >= {a, b, c}


def test_bar_read_without_chinese_is_noise_not_a_story():
    # 11/06 ETTV 12:35: OCR read "(0UU6" for 25 s before an ad and it became "0UU6/廣告"
    obs = news(60, 0, "甲則新聞標題", "t1") + news(25, 60, "(0UU6", "t2", speaker="spk1")
    assert all("0UU6" not in " ".join(s.bar_texts) for s in segs_of(obs))


def test_untitled_segment_before_an_ad_merges_into_the_ad():
    from conftest import framed_ad
    obs = news(60, 0, "甲則新聞標題", "t1") + news(30, 60, "(0UU6", "t2", speaker="spk1")
    obs += framed_ad(40, 90, "(0UU6") + news(30, 130, "乙則新聞標題", "t3")
    segs = segs_of(obs)
    assert [s.type for s in segs][:2] == [T.NEWS, T.AD]


# ---- AD-OVL, BRIEF-1, EVD-1/EVD-2 ----

def test_ADOVL_framed_ad_bar_text_never_opens_a_story():
    """Boxed picture + another ad signal: the frame's bar text is programme UI, not the story."""
    from newsbar.schemas import SecondObs
    o = SecondObs(0, bar_text="地方創生大展 週末登場", layout_changed=True, bar_color_changed=True)
    assert o.commercial_overlay
    # a news graphic that draws a frame edge while the ticker is on needs two more signals
    assert not SecondObs(0, bar_text="地方創生大展", layout_changed=True, bar_color_changed=True,
                         ticker_text="快訊").commercial_overlay
    # 9 s of framed-ad text: long enough to be "stable", too short to enter an ad (AD-1 needs 10 s)
    obs = news(40, 0, "新北土城七旬翁 遭貨車輾亡", "t1")
    obs += news(9, 40, "地方創生大展 週末登場", "t1", layout_changed=True, bar_color_changed=True, anchor_shot=True)
    obs += news(30, 49, "新北土城七旬翁 遭貨車輾亡", "t1")
    segs = segs_of(obs)
    assert [s.type for s in segs] == [T.NEWS]
    assert "地方創生大展 週末登場" not in segs[0].bar_texts
    assert segs[0].overlay_sec == 9 and segs[0].boundary["review_required"]


def test_ADOVL_frame_text_is_not_the_residual_title():
    from newsbar.ad_state import detect as det
    obs = news(60, 0, "新北土城七旬翁 遭貨車輾亡", "t1")
    obs += news(5, 60, "固力伸0800-030-031低至72折", "t1")  # ad copy before the break is confirmed
    obs += framed_ad(60, 65, "固力伸0800-030-031低至72折")
    obs += news(40, 125, "高金素梅共諜案起訴 求刑14年", "t2")
    brk = det(obs).breaks
    assert brk and brk[0].residual_bar == "新北土城七旬翁 遭貨車輾亡"


def test_BRIEF1_short_complete_brief_before_a_break_is_kept():
    """Anchor reads a 15 s brief (new title + anchor shot + its own speech topic), then a break."""
    obs = news(60, 0, "新北土城七旬翁 遭貨車輾亡", "t1")
    obs += news(15, 60, "立法院三讀 總預算案過關", "t5", anchor_shot=True)
    obs += framed_ad(60, 75, None)
    obs += news(40, 135, "高金素梅共諜案起訴 求刑14年", "t2", anchor_shot=True)
    segs = segs_of(obs)
    assert [s.type for s in segs] == [T.NEWS, T.NEWS, T.AD, T.NEWS]
    brief = segs[1]
    assert "BRIEF-1" in brief.signals and brief.boundary["evidence"]["brief"]
    assert brief.boundary["review_required"]


def test_EVD1_every_segment_carries_boundary_evidence_and_end():
    obs = news(40, 0, "新北土城七旬翁 遭貨車輾亡", "t1")
    obs += news(40, 40, "史詩崩跌台股殺3737點台積電一度跌停", "t2", anchor_shot=True)
    segs = segs_of(obs)
    assert len(segs) == 2
    b = segs[1].boundary
    assert b["evidence"]["title_change"] and b["evidence"]["topic_shift"] and b["evidence"]["anchor_shot"]
    assert b["boundary_probability"] == 0.9 and not b["review_required"]
    assert segs[0].end == 40 and segs[1].end == 80


def test_EVD2_title_only_change_is_flagged_for_review():
    obs = news(40, 0, "新北土城七旬翁 遭貨車輾亡", "t1", asr=None)
    obs += news(40, 40, "史詩崩跌台股殺3737點台積電一度跌停", "t1", asr=None)
    segs = segs_of(obs)
    assert segs[-1].boundary["boundary_probability"] == 0.55 and segs[-1].boundary["review_required"]
