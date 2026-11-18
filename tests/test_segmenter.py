"""spec §3, §4 — T08–T12."""

from conftest import news, stream

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


def test_FB1_new_text_without_evidence_still_changes_with_low_confidence():
    obs = news(30, 0, "新北土城七旬翁 遭貨車輾亡", "t1") + news(30, 30, "高金素梅共諜案起訴 求刑14年", "t1")
    segs = segs_of(obs)
    assert len(segs) == 2
    assert segs[1].confidence == "low" and segs[1].fallback == "FB-1"


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
