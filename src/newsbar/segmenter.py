"""Story-change detection (spec §3, §4, AD-8, FB-1–FB-3).

Works on one channel's per-second observations plus the ad_state result.
Only main_bar text is read (TXT-1); ticker, weather corner, LIVE tag and bare
shot cuts never trigger a change (TXT-2, BAN-1, BAN-2).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol

from . import lexicon
from .ad_state import AD, NEWS, OUTAGE, AdResult, carousel_seconds
from .schemas import Confidence, EventType, SecondObs


# Generic words that must not count as "same entity" on their own.
_STOPWORDS = {"新北", "台北", "台灣", "全台", "民眾", "今天", "記者", "現場", "最新", "快訊", "獨家"}


@dataclass(frozen=True)
class SegParams:
    min_conf: float = 0.0  # TXT-4 is applied in ocr.stream; raise only for un-voted input
    stable_sec: int = 7  # TIME-1: real 11/06 data — titles last ≥7 s, speech subtitles and name credits ≤6 s
    credit_max_sec: int = 5  # NEW-2d: a reporter-name credit flashes for ≤5 s …
    credit_window_sec: int = 25  # … and a title starting within 25 s after it is a new story
    asr_veto: bool = True  # SAME-6: with ASR, a new title without any NEW-2 evidence stays in the story
    carousel_window_sec: int = 120  # CAROUSEL: teaser loop detection
    min_story_before_ad_sec: int = 20
    recur_window_sec: int = 180
    carousel_min_titles: int = 6  # normal FTV stories rotate 2–4 titles; teaser loops show 10
    carousel_max_sec: int = 12
    asr_window_sec: int = 20  # NEW-2a
    visual_window_sec: int = 5  # NEW-2b, NEW-2c

    @classmethod
    def from_rules(cls, rules: dict | None) -> "SegParams":
        r = (rules or {}).get("segmenter", {})
        return cls(
            min_conf=cls.min_conf,
            stable_sec=r.get("stable_sec", cls.stable_sec),
            credit_max_sec=r.get("credit_max_sec", cls.credit_max_sec),
            credit_window_sec=r.get("credit_window_sec", cls.credit_window_sec),
            asr_veto=r.get("asr_veto", cls.asr_veto),
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
    frame: str | None = None  # main_bar crop at the start second

    @property
    def asr_text(self) -> str:
        return " ".join(t for t in self.asr_texts if t)


_CREDIT = re.compile(r"^[\u4e00-\u9fff]{2,12}$")
# Function characters that occur in speech subtitles but practically never in personal names.
_NOT_IN_NAMES = set("的了是我你他她們說這那會有在就也都要還不沒很個嗎呢吧啊喔啦因為所以如果但")


def is_credit(text: str | None) -> bool:
    """Reporter/anchor name credit: only CJK, 2–12 chars, no punctuation or digits."""
    return (bool(text) and bool(_CREDIT.match(text)) and not is_weather(text)  # type: ignore[arg-type]
            and not (set(text) & _NOT_IN_NAMES))  # type: ignore[arg-type]


def is_weather(text: str) -> bool:
    return any(k in text for k in lexicon.get().weather_words)  # config/lexicon.yaml: weather_words


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
    has_asr = any(o.asr_text for o in obs)

    carousel = carousel_seconds(obs, params)

    run_text: str | None = None
    run_start = 0
    run_len = 0
    run_topic: str | None = None
    run_topic_start = 0
    run_topic_len = 0
    last_credit: int | None = None

    def text_of(o: SecondObs) -> str | None:
        if o.bar_text is None or o.bar_conf < params.min_conf:
            return None
        # A Taiwanese news title always has Chinese; a read with fewer than two CJK
        # characters is OCR noise (11/06 ETTV: "(0UU6" before an ad became a story).
        if sum(len(r) for r in _CJK.findall(o.bar_text)) < 2:
            return None
        return o.bar_text

    def evidence(t: int) -> list[str]:
        nonlocal last_credit
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
        if last_credit is not None and 0 <= t - last_credit <= params.credit_window_sec:
            sigs.append("NEW-2d")
        return sigs

    def open_seg(seg: Segment) -> Segment:
        nonlocal cur, last_news_idx
        if seg.frame is None and seg.start in idx:
            seg.frame = obs[idx[seg.start]].frame
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
        if text is not None and (is_credit(text) or text.startswith(("主播", "記者"))) and cur is not None:
            return  # SAME-7: a name card is a boundary hint (NEW-2d), never a story of its own
        if text is None:  # FB-3: OCR missing, ASR topic drives the change
            open_seg(Segment(t, EventType.NEWS, signals=["NEW-2a"], confidence=Confidence.LOW.value, fallback="FB-3"))
            return
        sigs = evidence(t)
        # NEW-2c (speaker change) is recorded but not sufficient alone: interviews switch
        # speakers inside one story all the time (11/06 TVBS SOTs).
        low = not any(x in sigs for x in ("NEW-2a", "NEW-2b", "NEW-2d"))
        if low and has_asr and params.asr_veto and cur is not None and cur.type is EventType.NEWS:
            cur.bar_texts.append(text)  # SAME-6: speech topic unchanged, no credit, no anchor → same story
            cur.signals.append("SAME-6")
            return
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
                and (o.topic == brk.residual_topic if brk.residual_topic is not None else not has_asr)
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

        # Track the stable bar text (TIME-1); remember where a name credit ended (NEW-2d).
        if text is not None and text == run_text:
            run_len += 1
        else:
            if is_credit(run_text) and run_len <= params.credit_max_sec:
                last_credit = o.sec
            run_text, run_start, run_len = text, o.sec, 1
        # Track the stable ASR topic (used only when OCR is missing).
        if o.topic is not None and o.topic == run_topic:
            run_topic_len += 1
        else:
            run_topic, run_topic_start, run_topic_len = o.topic, o.sec, 1

        if run_text is not None and run_len == params.stable_sec and run_start in carousel:
            cur.signals.append("CAROUSEL")  # headline teaser loop before a break: no new stories
            continue
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
    # SAME-8: a title that comes back proves the segments in between were one story
    # (11/06 TVBS 法國罷工: 巴黎鐵塔關 → 封路生封校 → 百姓入不敷出 → 巴黎鐵塔關).
    def _sim(a: str, b: str) -> bool:
        from difflib import SequenceMatcher
        return a == b or SequenceMatcher(None, a, b).ratio() >= 0.8

    k = 0
    while k < len(segs):
        sj = segs[k]
        if sj.type is EventType.NEWS and sj.bar_texts:
            for i in range(k - 1, -1, -1):
                si = segs[i]
                if si.type is not EventType.NEWS or sj.start - si.start > params.recur_window_sec:
                    break
                if si.bar_texts and any(_sim(a, b) for a in si.bar_texts for b in sj.bar_texts) and i < k - 1:
                    for m in range(i + 1, k + 1):
                        si.bar_texts += [t for t in segs[m].bar_texts if t not in si.bar_texts]
                        si.asr_texts += segs[m].asr_texts
                    si.signals.append("SAME-8")
                    del segs[i + 1 : k + 1]
                    k = i
                    break
        k += 1

    # Ad copy just before a break (11/06: "watsog", "001536") opens a short pseudo-story
    # that the break then cuts off; anything under min_story_before_ad_sec merges into the ad.
    out: list[Segment] = []
    for k, sg in enumerate(segs):
        nxt = segs[k + 1] if k + 1 < len(segs) else None
        if (sg.type is EventType.NEWS and nxt is not None and nxt.type is EventType.AD
                and (nxt.start - sg.start < params.min_story_before_ad_sec or not sg.bar_texts)
                and sg.reuse_of is None and out):
            # a "story" with no title ever read before a break is ad copy (11/06 ETTV 12:35)
            continue
        out.append(sg)
    return out
