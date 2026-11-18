"""spec §5 — T05–T07, FB-5/FB-6."""

from conftest import framed_ad, news, stream

from newsbar.ad_state import AD, NEWS, OUTAGE, detect


def states_between(res, a, b):
    return {res.states[s] for s in range(a, b)}


def test_T05_corner_ad_does_not_enter_ad():
    obs = news(60, 0, "新北土城七旬翁 遭貨車輾亡", "t1", corner_ad=True)
    res = detect(obs)
    assert res.breaks == []
    assert states_between(res, 0, 60) == {NEWS}


def test_T06_framed_ad_with_residual_bar_is_ad():
    bar = "新北土城七旬翁 遭貨車輾亡"
    obs = news(30, 0, bar, "t1") + framed_ad(40, 30, bar) + news(30, 70, "高金素梅共諜案起訴 求刑14年", "t2")
    res = detect(obs)
    assert len(res.breaks) == 1
    b = res.breaks[0]
    assert b.start == 30 and b.end == 70
    assert b.residual_bar == bar
    assert states_between(res, 30, 70) == {AD}
    assert states_between(res, 70, 100) == {NEWS}


def test_single_signal_is_not_enough():
    obs = news(30, 0, "甲則新聞標題", "t1") + news(30, 30, "甲則新聞標題", "t1", black=True)
    assert detect(obs).breaks == []


def test_short_strong_burst_is_not_ad():
    obs = news(20, 0, "甲則新聞標題", "t1") + framed_ad(6, 20, "甲則新聞標題") + news(20, 26, "甲則新聞標題", "t1")
    assert detect(obs).breaks == []


def test_T07_layout_back_but_residual_bar_and_no_speech_waits_then_times_out():
    bar = "新北土城七旬翁 遭貨車輾亡"
    after = stream(90, 60, bar_text=bar, topic=None, asr_text=None)  # layout restored, residual text, silence
    obs = news(20, 0, bar, "t1") + framed_ad(40, 20, bar) + after
    res = detect(obs)
    b = res.breaks[0]
    assert b.timed_out
    assert b.end == 60 + 60  # AD-9: 60 s after the layout came back
    assert res.states[100] == AD and res.states[125] == NEWS


def test_return_same_story_confirmed_by_topic():
    bar = "新北土城七旬翁 遭貨車輾亡"
    obs = news(20, 0, bar, "t1") + framed_ad(30, 20, bar) + news(30, 50, bar, "t1")
    b = detect(obs).breaks[0]
    assert b.end == 50 and not b.timed_out


def test_FB6_long_black_is_outage_not_ad():
    obs = news(10, 0, "甲則新聞標題", "t1") + stream(40, 10, black=True) + news(10, 50, "甲則新聞標題", "t1")
    res = detect(obs)
    assert states_between(res, 10, 50) == {OUTAGE}
    assert res.breaks == []


def test_breaks_shorter_than_25_seconds_are_dropped():
    # 11/06 TVBS: 13 s programme intro (no clock, no ticker) at 11:59:47 is not an ad break
    obs = news(60, 0, "甲則新聞標題", "t1", ticker_text="快訊", frame="f")
    obs += stream(13, 60, ticker_text=None, frame="f", clock_ok=False)
    obs += news(60, 73, "乙則新聞標題", "t2", ticker_text="快訊", frame="f")
    assert detect(obs).breaks == []
