"""Story-change detection (spec §3, §4, AD-8, FB-1–FB-3).

Works on one channel's per-second observations plus the ad_state result.
Only main_bar text is read (TXT-1); ticker, weather corner, LIVE tag and bare
shot cuts never trigger a change (TXT-2, BAN-1, BAN-2).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol

from .ad_state import AD, NEWS, OUTAGE, AdResult
from .schemas import Confidence, EventType, SecondObs

WEATHER_KEYWORDS = ("天氣", "氣象", "颱風動態", "降雨", "氣溫", "寒流", "鋒面")

# Generic words that must not count as "same entity" on their own.
_STOPWORDS = {"新北", "台北", "台灣", "全台", "民眾", "今天", "記者", "現場", "最新", "快訊", "獨家"}


@dataclass(frozen=True)
class SegParams:
    min_conf: float = 0.6  # TXT-4
    stable_sec: int = 3  # TIME-1
    asr_window_sec: int = 20  # NEW-2a
    visual_window_sec: int = 5  # NEW-2b, NEW-2c

    @classmethod
    def from_rules(cls, rules: dict | None) -> "SegParams":
        r = (rules or {}).get("segmenter", {})
        o = (rules or {}).get("ocr", {})
        return cls(
            min_conf=o.get("min_conf", cls.min_conf),
            stable_sec=r.get("stable_sec", cls.stable_sec),
            asr_window_sec=r.get("asr_window_sec", cls.asr_window_sec),
            visual_window_sec=r.get("visual_window_sec", cls.visual_window_sec),
        )


class SameStoryJudge(Protocol):
    def same(self, text: str, story_texts: list[str]) -> bool: ...


_CJK = re.compile(r"[一-鿿]+")


class LexicalJudge:
    """Baseline for SAME-3/SAME-4: shares a ≥2-char CJK term not in the stoplist.

    Replace with an embedding judge (cosine ≥ same_story_cosine) in production.
    """

    def __init__(self, min_common: int = 2):
        self.min_common = min_common

    def _terms(self, text: str) -> set[str]:
        out: set[str] = set()
        for run in _CJK.findall(text):
            for i in range(len(run) - self.min_common + 1):
                t = run[i : i + self.min_common]
                if t not in _STOPWORDS:
                    out.add(t)
        return out

    def same(self, text: str, story_texts: list[str]) -> bool:
        terms = self._terms(text)
        return any(terms & self._terms(s) for s in story_texts)


@dataclass
class Segment:
    start: int
    type: EventType
    bar_texts: list[str] = field(default_factory=list)
    asr_texts: list[str] = field(default_factory=list)
    signals: list[str] = field(default_factory=list)
    confidence: str = Confidence.HIGH.value
    fallback: str | None = None
    reuse_of: int | None = None  # index of an earlier segment this continues (AD-8)

    @property
    def asr_text(self) -> str:
        return " ".join(t for t in self.asr_texts if t)


def is_weather(text: str) -> bool:
    return any(k in text for k in WEATHER_KEYWORDS)


def segment(
    obs: list[SecondObs],
    ad: AdResult,
    params: SegParams = SegParams(),
    judge: SameStoryJudge | None = None,
) -> list[Segment]:
    judge = judge or LexicalJudge()
    obs = sorted(obs, key=lambda o: o.sec)
    idx = {o.sec: i for i, o in enumerate(obs)}
    segs: list[Segment] = []
    cur: Segment | None = None
    last_news_idx: int | None = None  # last NEWS/WEATHER segment before a break
    breaks = {b.end: b for b in ad.breaks}

    run_text: str | None = None
    run_start = 0
    run_len = 0
    run_topic: str | None = None
    run_topic_start = 0
    run_topic_len = 0

    def text_of(o: SecondObs) -> str | None:
        if o.bar_text is None or o.bar_conf < params.min_conf:
            return None
        return o.bar_text

    def evidence(t: int) -> list[str]:
        i = idx[t]
        sigs = ["NEW-1"]
        w = params.asr_window_sec
        before = {o.topic for o in obs[max(0, i - w) : i] if o.topic}
        after = {o.topic for o in obs[i : i + w] if o.topic}
        if before and after and not (before & after):
            sigs.append("NEW-2a")
        v = params.visual_window_sec
        win = obs[max(0, i - v) : i + v + 1]
        if any(o.anchor_shot for o in win):
            sigs.append("NEW-2b")
        speakers = [o.speaker for o in win if o.speaker]
        if len(set(speakers)) > 1:
            sigs.append("NEW-2c")
        return sigs

    def open_seg(seg: Segment) -> Segment:
        nonlocal cur, last_news_idx
        segs.append(seg)
        cur = seg
        if seg.type in (EventType.NEWS, EventType.WEATHER):
            last_news_idx = len(segs) - 1
        return seg

    def open_story(t: int, text: str | None) -> None:
        if text is not None and is_weather(text):
            if cur is None or cur.type is not EventType.WEATHER:
                open_seg(Segment(t, EventType.WEATHER, bar_texts=[text], signals=["TXT-5"]))
            return
        if text is None:  # FB-3: OCR missing, ASR topic drives the change
            open_seg(Segment(t, EventType.NEWS, signals=["NEW-2a"], confidence=Confidence.LOW.value, fallback="FB-3"))
            return
        sigs = evidence(t)
        low = len(sigs) == 1
        open_seg(
            Segment(
                t,
                EventType.NEWS,
                bar_texts=[text],
                signals=sigs,
                confidence=Confidence.LOW.value if low else Confidence.HIGH.value,
                fallback="FB-1" if low else None,
            )
        )

    for o in obs:
        state = ad.states.get(o.sec, NEWS)
        if state in (AD, OUTAGE):
            etype = EventType.AD if state == AD else EventType.OUTAGE
            if cur is None or cur.type is not etype:
                open_seg(Segment(o.sec, etype, signals=o.ad_signals() or ["FB-6"]))
            run_text, run_len, run_topic, run_topic_len = None, 0, None, 0
            continue

        # ---- news second ----
        text = text_of(o)
        returning = cur is not None and cur.type in (EventType.AD, EventType.OUTAGE)
        if returning or cur is None:
            brk = breaks.get(o.sec)
            prev = segs[last_news_idx] if last_news_idx is not None else None
            if (
                brk is not None
                and prev is not None
                and text is not None
                and text == brk.residual_bar
                and brk.residual_topic is not None
                and o.topic == brk.residual_topic
            ):
                # AD-8: same story resumes → rewrite its label at the return minute.
                open_seg(Segment(o.sec, prev.type, bar_texts=[text], signals=["AD-7", "AD-8"], reuse_of=last_news_idx))
            elif brk is not None and brk.timed_out and (text is None or text == brk.residual_bar):
                # AD-9: back without fresh bar text → name it from speech (FB-3).
                open_seg(Segment(o.sec, EventType.NEWS, signals=["AD-9"], confidence=Confidence.LOW.value, fallback="FB-3"))
            elif cur is None and text is None:
                open_story(o.sec, None)
            else:
                open_story(o.sec, text)
            run_text, run_start, run_len = text, o.sec, 1
            run_topic, run_topic_start, run_topic_len = o.topic, o.sec, 1
            if o.asr_text:
                cur.asr_texts.append(o.asr_text)  # type: ignore[union-attr]
            continue

        assert cur is not None
        if o.asr_text and (not cur.asr_texts or cur.asr_texts[-1] != o.asr_text):
            cur.asr_texts.append(o.asr_text)

        # Track the stable bar text (TIME-1).
        if text is not None and text == run_text:
            run_len += 1
        else:
            run_text, run_start, run_len = text, o.sec, 1
        # Track the stable ASR topic (used only when OCR is missing).
        if o.topic is not None and o.topic == run_topic:
            run_topic_len += 1
        else:
            run_topic, run_topic_start, run_topic_len = o.topic, o.sec, 1

        if run_text is not None and run_len == params.stable_sec:
            if run_text in cur.bar_texts:
                continue
            if cur.type is EventType.WEATHER and is_weather(run_text):
                cur.bar_texts.append(run_text)
                continue
            if cur.type is EventType.NEWS and cur.bar_texts and judge.same(run_text, cur.bar_texts):
                cur.bar_texts.append(run_text)  # SAME-3 / SAME-4
                continue
            if cur.type is EventType.NEWS and not cur.bar_texts:
                cur.bar_texts.append(run_text)  # first bar reading of an ASR-opened story
                continue
            open_story(run_start, run_text)
            if o.asr_text:
                cur.asr_texts.append(o.asr_text)  # type: ignore[union-attr]
        elif (
            run_text is None
            and not cur.bar_texts
            and run_topic is not None
            and run_topic_len == params.stable_sec
            and cur.fallback == "FB-3"
            and run_topic_start > cur.start
        ):
            open_story(run_topic_start, None)  # FB-3: topic change with no OCR
            if o.asr_text:
                cur.asr_texts.append(o.asr_text)  # type: ignore[union-attr]
    return segs
