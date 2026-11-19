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


def voice(f0, seconds=2.0, sr=16000):
    import numpy as np
    t = np.arange(int(seconds * sr)) / sr
    return sum(np.sin(2 * np.pi * f0 * k * t) / k for k in range(1, 6)).astype("float32") * 0.2


def test_speaker_change_by_pitch():
    import numpy as np
    from newsbar.asr import assign_speakers, segment_pitch

    sr = 16000
    pcm = np.concatenate([voice(120), voice(125), voice(230), voice(225)])
    segs = [AsrSegment(i * 2, i * 2 + 2, "x") for i in range(4)]
    pitches = [segment_pitch(pcm, sr, s) for s in segs]
    assert abs(pitches[0] - 120) < 6 and abs(pitches[2] - 230) < 10
    assert assign_speakers(pitches) == ["spk0", "spk0", "spk1", "spk1"]


def test_guard_runs_fake_backends_in_process():
    from pathlib import Path
    from newsbar.asr import transcribe_guarded

    class Fake:
        name = "fake"
        def transcribe(self, wav):
            return [AsrSegment(0, 1, "x")]

    assert transcribe_guarded(Fake(), Path("a.wav"), 10)[0].text == "x"
