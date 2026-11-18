"""OCR voting and scrolling guard (TXT-3, TXT-4) — no OCR engine needed."""

from newsbar.ocr.base import OcrLine, join_lines
from newsbar.ocr.stream import RawRead, normalize, scrolling_spans, vote

A = "新北土城七旬翁遭貨車輾亡"
B = "高金素梅共諜案起訴求刑14年"


def reads(pattern):
    return [RawRead(i, t, c) for i, (t, c) in enumerate(pattern)]


def test_normalize_strips_spaces_and_width():
    assert normalize(" 新北土城 七旬翁　遭貨車輾亡 ") == A
    assert normalize("ＴＶＢＳ") == "TVBS"
    assert normalize("  ") is None


def test_low_conf_reads_dropped():
    out = vote(reads([(A, 0.9)] * 3 + [(B, 0.3)] * 5))
    assert out[-1].text != B


def test_single_noisy_frame_is_outvoted():
    noisy = "新北土城七旬翁遭貨車輾芒"  # one wrong char
    out = vote(reads([(A, 0.9)] * 4 + [(noisy, 0.9)] + [(A, 0.9)] * 4))
    assert {r.text for r in out} == {A}


def test_noise_snaps_to_previous_reading():
    noisy = "新北土城七旬翁遭貨車輾芒"
    out = vote(reads([(A, 0.9)] * 5 + [(noisy, 0.9)] * 5))
    assert {r.text for r in out} == {A}


def test_real_change_survives_voting():
    out = vote(reads([(A, 0.9)] * 6 + [(B, 0.9)] * 6))
    texts = [r.text for r in out]
    assert texts[0] == A and texts[-1] == B
    assert texts.index(B) in (5, 6, 7)


def test_short_dropout_is_filled():
    out = vote(reads([(A, 0.9)] * 4 + [(None, 0.0)] + [(A, 0.9)] * 4))
    assert all(r.text == A for r in out)


def test_long_dropout_stays_blank():
    out = vote(reads([(None, 0.0)] * 10))
    assert all(r.text is None for r in out)


def test_scrolling_text_is_flagged():
    long = "颱風動態最新消息北部山區注意豪雨特報今晚起影響全台各地"
    pattern = [(long[i : i + 12], 0.9) for i in range(12)]
    assert scrolling_spans(reads(pattern))


def test_static_bar_not_flagged_as_scrolling():
    assert scrolling_spans(reads([(A, 0.9)] * 6 + [(B, 0.9)] * 6)) == []


def test_join_lines_reading_order():
    lines = [OcrLine("副標", 0.9, (0.1, 0.6, 0.2, 0.3)), OcrLine("主標右", 0.8, (0.5, 0.1, 0.3, 0.3)),
             OcrLine("主標左", 0.9, (0.1, 0.12, 0.3, 0.3))]
    text, conf = join_lines(lines)
    assert text == "主標左 主標右 副標"
    assert 0.8 < conf <= 0.9
