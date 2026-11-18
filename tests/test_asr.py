"""ASR post-processing: topics, ad speech, per-second attach (no model needed)."""

from newsbar.asr import AsrSegment, assign_topics, attach, is_ad_speech
from newsbar.schemas import SecondObs

STORY_A = [
    AsrSegment(0, 6, "新北土城一名七旬老翁今天騎車轉彎時遭貨車輾過"),
    AsrSegment(6, 12, "老翁送醫後仍不治 貨車司機酒測值為零"),
    AsrSegment(12, 18, "警方調閱監視器釐清土城車禍肇事責任"),
]
STORY_B = [
    AsrSegment(18, 24, "高金素梅共諜案今天由檢方起訴並求刑十四年"),
    AsrSegment(24, 30, "檢方指出張俊傑以九分鐘偷渡術往返兩岸"),
    AsrSegment(30, 36, "高金素梅辦公室表示將在法庭上說明"),
]


def test_topic_changes_between_stories():
    topics = assign_topics(STORY_A + STORY_B)
    assert len(set(topics[:3])) == 1
    assert len(set(topics[3:])) == 1
    assert topics[0] != topics[3]


def test_ad_speech_detection():
    assert is_ad_speech("限時特價只要九九九元 現在打電話")
    assert is_ad_speech("全館免運 加贈好禮")
    assert not is_ad_speech("老翁送醫後仍不治")


def test_attach_fills_seconds():
    obs = [SecondObs(sec=s - 5) for s in range(40)]
    attach(obs, backend=None, sec_offset=-5, segments=STORY_A + STORY_B)
    by = {o.sec: o for o in obs}
    assert by[-5].asr_text == STORY_A[0].text
    assert by[-5].topic == by[12].topic != by[14].topic
    assert not any(o.ad_speech for o in obs)
