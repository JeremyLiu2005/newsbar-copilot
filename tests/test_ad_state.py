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


def test_AD7_AD8_residual_title_allows_ocr_noise():
    from newsbar.ad_state import same_title
    a = "高金素梅共諜案起訴 求刑14年"
    assert same_title(a, "高金素梅共諜案起訴 求刑l4年")  # one character misread
    assert not same_title(a, "史詩崩跌台股殺3737點")
    assert not same_title(a, "高金素梅共諜案起訴 求刑l4年", ratio=1.0)  # 1.0 keeps the old exact rule
    assert not same_title(None, a)


def test_AD6_residual_title_skips_credits():
    from newsbar.ad_state import title_like
    assert not title_like("高沛羚郭南宏台北報導") and not title_like("綜合報導") and not title_like("洪儀婷程奕翔")
    assert not title_like("00415:361.9") and not title_like("主播古彩彥")
    assert title_like("高歌離席!C羅歸還7號戰袍結束國家隊傳奇")


def test_AD9_full_screen_break_is_not_cut_by_the_timeout():
    """A full-screen commercial never boxes the picture: the 60 s timeout must not start at its
    first second (11/06: 10 breaks were cut into 60 s pieces and glued back)."""
    obs = news(60, 0, "新北土城七旬翁 遭貨車輾亡", "t1", ticker_text="快訊", frame="f.jpg", clock_ok=True)
    obs += news(150, 60, None, None, asr="限時特價 現在打電話", ad_speech=True, ticker_text=None, frame="f.jpg",
                clock_ok=False, logo_missing=True)
    obs += news(40, 210, "高金素梅共諜案起訴 求刑14年", "t2", ticker_text="快訊", frame="f.jpg", clock_ok=True)
    brk = detect(obs).breaks
    assert len(brk) == 1 and not brk[0].timed_out and abs(brk[0].end - 210) <= 1


def test_AD7_own_overlay_back_needs_three_signals_to_stay_ad():
    """Ticker and clock back with a fresh headline: logo misread + a stray fingerprint hit is not an ad."""
    obs = news(60, 0, "新北土城七旬翁 遭貨車輾亡", "t1", ticker_text="快訊", frame="f.jpg", clock_ok=True)
    obs += news(100, 60, None, None, asr="限時特價", ad_speech=True, ticker_text=None, frame="f.jpg", clock_ok=False,
                fingerprint_hit=True)
    obs += news(40, 160, "高金素梅共諜案起訴 求刑14年", "t2", ticker_text="快訊", frame="f.jpg", clock_ok=True,
                logo_missing=True, fingerprint_hit=True)
    brk = detect(obs).breaks
    assert brk and abs(brk[0].end - 160) <= 1
