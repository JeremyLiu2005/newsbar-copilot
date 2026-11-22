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
from .ad_state import AD, NEWS, OUTAGE, AdResult, carousel_seconds, same_title
from .schemas import Confidence, EventType, SecondObs, is_ad_copy


MIN_CJK_CHARS = 2  # 規則 TXT-7：主標題讀數至少要有 2 個中文字，否則當雜訊

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
    recur_min_terms: int = 2  # SAME-8: shared two-character terms that also count as "the title came back"
    carousel_min_titles: int = 6  # normal FTV stories rotate 2–4 titles; teaser loops show 10
    carousel_max_sec: int = 12
    asr_window_sec: int = 20  # NEW-2a
    visual_window_sec: int = 5  # NEW-2b, NEW-2c
    speech_window_sec: int = 30  # NEW-2e: speech after a candidate checked against the titles
    speech_min_terms: int = 2  # NEW-2e: the new title's words heard at least this many times
    speech_tail_max_sec: int = 15  # SAME-9: a story this short, opened on speech evidence alone, is a tail
    mode: str = "rules"  # "rules" (default) | "learned": a trained probability decides story changes
    model: str | None = None  # learned: model file, or "active" (data/models/active.json)

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
            speech_window_sec=r.get("speech_window_sec", cls.speech_window_sec),
            speech_min_terms=r.get("speech_min_terms", cls.speech_min_terms),
            speech_tail_max_sec=r.get("speech_tail_max_sec", cls.speech_tail_max_sec),
            mode=r.get("mode", cls.mode),
            model=r.get("model", cls.model),
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
    end: int | None = None  # exclusive: start of the next segment (or end of the stream)
    overlay_sec: int = 0  # AD-OVL: seconds whose bar text was framed-ad copy and was ignored
    boundary_p: float | None = None  # EVD-1: learned model's raw score for this boundary (None under the rules)
    boundary: dict = field(default_factory=dict)  # EVD-1: evidence behind this boundary

    @property
    def asr_text(self) -> str:
        return " ".join(t for t in self.asr_texts if t)


def _ratio(a: str, b: str) -> float:
    from difflib import SequenceMatcher

    return 1.0 if a == b else SequenceMatcher(None, a, b).ratio()


def _terms(text: str) -> set[str]:
    """Two-character Chinese terms of a title, generic words removed (NEW-2e, SAME-3)."""
    out: set[str] = set()
    for run in _CJK.findall(text or ""):
        out.update(t for t in (run[i:i + 2] for i in range(len(run) - 1)) if t not in _STOPWORDS)
    return out


_CREDIT = re.compile(r"^[\u4e00-\u9fff]{2,12}$")
# Function characters that occur in speech subtitles but practically never in personal names.
_NOT_IN_NAMES = set("的了是我你他她們說這那會有在就也都要還不沒很個嗎呢吧啊喔啦因為所以如果但")


def is_credit(text: str | None) -> bool:
    """Reporter/anchor name credit: only CJK, 2–12 chars, no punctuation or digits."""
    return (bool(text) and bool(_CREDIT.match(text)) and not is_weather(text)  # type: ignore[arg-type]
            and not (set(text) & _NOT_IN_NAMES))  # type: ignore[arg-type]


def is_weather(text: str) -> bool:
    return any(k in text for k in lexicon.get().weather_words)  # config/lexicon.yaml: weather_words


CHANGE_FEATURES = ["run_len", "new2a", "new2b", "new2c", "new2d", "sim", "lex_same", "seen180",
                   "asr_present", "credit_flash", "since_last"]


def segment(
    obs: list[SecondObs],
    ad: AdResult,
    params: SegParams = SegParams(),
    judge: SameStoryJudge | None = None,
    recorder: list | None = None,
) -> list[Segment]:
    """`recorder` (ML dataset building): every change candidate — a stable title that is not already
    in the current story — appends {"sec", "features", "rules_new"}; it never alters the result."""
    judge = judge or LexicalJudge()
    learned = None
    if params.mode == "learned":
        from .mlmodel import load_model

        if not params.model:
            raise ValueError("segmenter.mode: learned 需要 segmenter.model（模型檔路徑或 active）")
        learned = load_model(params.model, "change")
    elif params.mode != "rules":
        raise ValueError(f"unknown segmenter.mode {params.mode!r}")
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

    # 規則 TXT-1、TXT-7：只讀主標題條；中文字少於 2 個的讀數視為 OCR 雜訊
    def text_of(o: SecondObs) -> str | None:
        if o.bar_text is None or o.bar_conf < params.min_conf:
            return None
        # 規則 AD-OVL：包框廣告時外框上的字不是這則新聞的標題 → 當作沒讀到
        if o.commercial_overlay:
            return None
        # A Taiwanese news title always has Chinese; a read with fewer than two CJK
        # characters is OCR noise (11/06 ETTV: "(0UU6" before an ad became a story).
        if sum(len(r) for r in _CJK.findall(o.bar_text)) < MIN_CJK_CHARS:
            return None
        return o.bar_text

    def speech_follows(i: int, text: str) -> bool:
        """NEW-2e: the speech right after the candidate repeats the new title's words, while the
        current story's title words are no longer heard. ASR topic ids change with almost every utterance
        (11/06 FTV: t347→t353 in 40 s), so NEW-2a rarely sees two disjoint windows; the words do."""
        if cur is None or not cur.bar_texts:
            return False
        new = _terms(text) - set().union(*(_terms(b) for b in cur.bar_texts))
        old = set().union(*(_terms(b) for b in cur.bar_texts)) - _terms(text)
        heard = " ".join(dict.fromkeys(o.asr_text for o in obs[i : i + params.speech_window_sec] if o.asr_text))
        if not heard or not new:
            return False
        n_new = sum(1 for w in new if w in heard)
        n_old = sum(1 for w in old if w in heard)
        # the old story's words must be (almost) gone: a reporter still saying 巴拉圭總統 while the title
        # rotates to 存款領不回來 is the same story, however many of the new words are heard too
        return n_new >= params.speech_min_terms and n_old <= 1

    def evidence(t: int, text: str | None = None) -> list[str]:
        nonlocal last_credit
        i = idx[t]
        sigs = ["NEW-1"]
        w = params.asr_window_sec
        before = {o.topic for o in obs[max(0, i - w) : i] if o.topic}
        after = {o.topic for o in obs[i : i + w] if o.topic}
        # 規則 NEW-2a：前後 20 秒語音主題明顯不同 → 佐證
        if before and after and not (before & after):
            sigs.append("NEW-2a")
        v = params.visual_window_sec
        win = obs[max(0, i - v) : i + v + 1]
        # 規則 NEW-2b：前後 5 秒畫面切回主播台 → 佐證
        if any(o.anchor_shot for o in win):
            sigs.append("NEW-2b")
        speakers = [o.speaker for o in win if o.speaker]
        # 規則 NEW-2c：講話的人換了（只記錄，不能單獨算數）
        if len(set(speakers)) > 1:
            sigs.append("NEW-2c")
        # 規則 NEW-2d：記者名條閃過後 25 秒內開始的標題 → 佐證
        if last_credit is not None and 0 <= t - last_credit <= params.credit_window_sec:
            sigs.append("NEW-2d")
        # 規則 NEW-2e：候選點之後 30 秒的語音，講的是新標題的詞，而且比舊標題的詞多 → 佐證
        if text is not None and speech_follows(i, text):
            sigs.append("NEW-2e")
        return sigs

    def candidate_features(t: int, text: str) -> dict:
        """Features of "does the title that starts at t open a new story?" (change candidates only)."""
        i = idx[t]
        j = i
        while j < len(obs) and text_of(obs[j]) == text and ad.states.get(obs[j].sec, NEWS) == NEWS and j - i < 120:
            j += 1
        sigs = evidence(t)
        v = params.visual_window_sec
        win = obs[max(0, i - v) : i + v + 1]
        assert cur is not None
        recent = [b for sg in segs if t - sg.start <= params.recur_window_sec for b in sg.bar_texts]
        return {
            "run_len": float(j - i),
            "new2a": float("NEW-2a" in sigs), "new2b": float("NEW-2b" in sigs),
            "new2c": float("NEW-2c" in sigs), "new2d": float("NEW-2d" in sigs),
            "sim": max((_ratio(text, b) for b in cur.bar_texts), default=0.0),
            "lex_same": float(judge.same(text, cur.bar_texts)),
            "seen180": float(any(_ratio(text, b) >= 0.8 for b in recent)),
            "asr_present": float(any(o.asr_text for o in win)),
            "credit_flash": float(last_credit is not None and 0 <= t - last_credit <= 3),
            "since_last": float(min(t - cur.start, 300)),
        }

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
        # 規則 TXT-5：標題是天氣 → 這一格寫「氣象」
        if text is not None and is_weather(text):
            if cur is None or cur.type is not EventType.WEATHER:
                open_seg(Segment(t, EventType.WEATHER, bar_texts=[text], signals=["TXT-5"]))
            return
        # 規則 SAME-7：名條、「主播／記者」開頭的字樣不是新的一則，只當換題線索
        if text is not None and (is_credit(text) or text.startswith(("主播", "記者"))) and cur is not None:
            return  # SAME-7: a name card is a boundary hint (NEW-2d), never a story of its own
        # 規則 FB-3：讀不到標題 → 用語音主題換題並命名，標低信心
        if text is None:  # FB-3: OCR missing, ASR topic drives the change
            open_seg(Segment(t, EventType.NEWS, signals=["NEW-2a"], confidence=Confidence.LOW.value, fallback="FB-3"))
            return
        sigs = evidence(t, text)
        # NEW-2c (speaker change) is recorded but not sufficient alone: interviews switch
        # speakers inside one story all the time (11/06 TVBS SOTs).
        # 規則 FB-1：只有「標題變了」沒有其他佐證 → 仍換題但標低信心
        low = not any(x in sigs for x in ("NEW-2a", "NEW-2b", "NEW-2d", "NEW-2e"))
        # 規則 SAME-6：有語音但沒有任何佐證 → 留在同一則，不換題
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
                and same_title(text, brk.residual_bar, ad.residual_match_ratio)  # 規則 AD-8：允許 OCR 差一兩個字
                and (o.topic == brk.residual_topic if brk.residual_topic is not None else not has_asr)
            ):
                # 規則 AD-8：回來的還是殘留標題且語音確認同一則 → 沿用廣告前那則的標籤
                # AD-8: same story resumes → rewrite its label at the return minute.
                open_seg(Segment(o.sec, prev.type, bar_texts=[text], signals=["AD-7", "AD-8"], reuse_of=last_news_idx))
            elif brk is not None and brk.timed_out and (text is None or same_title(text, brk.residual_bar, ad.residual_match_ratio)):
                # 規則 AD-9：逾時回新聞又沒有新標題 → 用語音命名，標低信心
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
        if o.commercial_overlay:
            cur.overlay_sec += 1
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

        # 規則 CAROUSEL：標題輪播期間不換題
        if run_text is not None and run_len == params.stable_sec and run_start in carousel:
            cur.signals.append("CAROUSEL")  # headline teaser loop before a break: no new stories
            continue
        # 規則 TIME-1：連續 7 秒讀到同一標題才算「穩定出現」，換題時間取第一秒
        if run_text is not None and run_len == params.stable_sec:
            if run_text in cur.bar_texts:
                continue
            if cur.type is EventType.WEATHER and is_weather(run_text):
                cur.bar_texts.append(run_text)
                continue
            # A change candidate: a stable title inside a running news story, not weather, not a name card.
            # 規則 NEW-1：穩定的新標題、不是天氣也不是名條 → 換題候選
            cand = (cur.type is EventType.NEWS and bool(cur.bar_texts) and not is_weather(run_text)
                    and not (is_credit(run_text) or run_text.startswith(("主播", "記者"))))
            feats = candidate_features(run_start, run_text) if cand and (learned or recorder is not None) else None
            if learned and feats is not None:
                p = learned.predict(feats)
                if recorder is not None:
                    recorder.append({"sec": run_start, "features": feats, "p": round(p, 4)})
                if p < learned.threshold:
                    cur.bar_texts.append(run_text)
                    cur.signals.append("ML-same")
                    continue
                sigs = [k for k, f in (("NEW-1", True), ("NEW-2a", feats["new2a"]), ("NEW-2b", feats["new2b"]),
                                       ("NEW-2c", feats["new2c"]), ("NEW-2d", feats["new2d"])) if f]
                open_seg(Segment(run_start, EventType.NEWS, bar_texts=[run_text], signals=sigs + ["ML-new"],
                                 confidence=Confidence.HIGH.value, boundary_p=round(p, 3)))
                if o.asr_text:
                    cur.asr_texts.append(o.asr_text)  # type: ignore[union-attr]
                continue
            n_before = len(segs)
            # 規則 SAME-3：新舊標題共有連續兩個中文字 → 同一則的主副標輪播，不換題
            if cur.type is EventType.NEWS and cur.bar_texts and judge.same(run_text, cur.bar_texts):
                cur.bar_texts.append(run_text)  # SAME-3 / SAME-4
                if feats is not None and recorder is not None:
                    recorder.append({"sec": run_start, "features": feats, "rules_new": False})
                continue
            if cur.type is EventType.NEWS and not cur.bar_texts:
                cur.bar_texts.append(run_text)  # first bar reading of an ASR-opened story
                continue
            open_story(run_start, run_text)
            if feats is not None and recorder is not None:
                recorder.append({"sec": run_start, "features": feats, "rules_new": len(segs) > n_before})
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
    # 規則 SAME-8：180 秒內標題又回來 → 中間的段落併成同一則
    def _sim(a: str, b: str) -> bool:
        from difflib import SequenceMatcher
        # the same title back (≥0.8 alike), or a title sharing two content terms with it
        # (11/06 TVBS 旅展: 旅展促銷餐券買1送1 … 五星酒店餐券買1送1 → 餐券、券買)
        return (a == b or SequenceMatcher(None, a, b).ratio() >= 0.8
                or len(_terms(a) & _terms(b)) >= params.recur_min_terms)

    k = 0
    while k < len(segs):
        sj = segs[k]
        if sj.type is EventType.NEWS and sj.bar_texts:
            for i in range(k - 1, -1, -1):
                si = segs[i]
                if si.type is not EventType.NEWS or sj.start - si.start > params.recur_window_sec:
                    break
                # only across at least one segment: two neighbouring stories often share words without
                # being one story (11/06 TVBS 台北兒虐 → 沈蔣拜票, 台股崩跌 → 美股崩跌)
                if si.bar_texts and any(_sim(a, b) for a in si.bar_texts for b in sj.bar_texts) and i < k - 1:
                    for m in range(i + 1, k + 1):
                        si.bar_texts += [t for t in segs[m].bar_texts if t not in si.bar_texts]
                        si.asr_texts += segs[m].asr_texts
                    si.signals.append("SAME-8")
                    del segs[i + 1 : k + 1]
                    k = i
                    break
        k += 1

    # 規則 SAME-9：只靠語音佐證（NEW-2e）開出來、又不到 15 秒就換題的「新聞」，是前一則的收尾
    # （11/06 華視河口湖案最後一句「憂有其他受害者」），併回前一則
    merged: list[Segment] = []
    for k, sg in enumerate(segs):
        nxt_start = segs[k + 1].start if k + 1 < len(segs) else None
        prev = merged[-1] if merged else None
        only_speech = (sg.type is EventType.NEWS and "NEW-2e" in sg.signals and sg.reuse_of is None
                       and not ({"NEW-2a", "NEW-2b", "NEW-2d", "ML-new"} & set(sg.signals)))
        if (only_speech and prev is not None and prev.type is EventType.NEWS and nxt_start is not None
                and nxt_start - sg.start < params.speech_tail_max_sec):
            prev.bar_texts += [t for t in sg.bar_texts if t not in prev.bar_texts]
            prev.asr_texts += sg.asr_texts
            prev.signals.append("SAME-9")
            continue
        merged.append(sg)
    segs = merged

    # Ad copy just before a break (11/06: "watsog", "001536") opens a short pseudo-story
    # that the break then cuts off; anything under min_story_before_ad_sec merges into the ad.
    # 規則 NEW-3：廣告前不到 20 秒、或從沒讀到標題的「新聞」其實是廣告文案 → 併入廣告
    # 規則 BRIEF-1：但「穩定標題＋換題佐證＋有新聞語音」的短播報是完整的一則，保留
    out: list[Segment] = []
    for k, sg in enumerate(segs):
        nxt = segs[k + 1] if k + 1 < len(segs) else None
        if (sg.type is EventType.NEWS and nxt is not None and nxt.type is EventType.AD
                and (nxt.start - sg.start < params.min_story_before_ad_sec or not sg.bar_texts)
                and sg.reuse_of is None and out):
            recent = [t for o in out if o.type is EventType.NEWS and sg.start - o.start <= params.recur_window_sec
                      for t in o.bar_texts]
            # a "brief" that shares a term with a story of the last 180 s is that story's tail, not its own story
            if is_brief(sg) and not _speech_runs_into(obs, sg, nxt) and not judge.same(sg.bar_texts[0], recent):
                sg.signals.append("BRIEF-1")
                out.append(sg)
                continue
            # a "story" with no title ever read before a break is ad copy (11/06 ETTV 12:35)
            continue
        out.append(sg)
    stream_end = obs[-1].sec + 1 if obs else 0
    for k, sg in enumerate(out):
        sg.end = out[k + 1].start if k + 1 < len(out) else stream_end
        _attach_boundary(sg, out[k - 1] if k else None)
    return out


BRIEF_AD_PROBE_SEC = 10  # BRIEF-1: seconds of the following break checked for the same speech topic


def _speech_runs_into(obs: list[SecondObs], sg: Segment, brk: Segment) -> bool:
    """BRIEF-1: the "brief" talks on the same speech topic as the opening of the break → it is
    the commercial's own copy and voice (11/06 "watsog" before an ad), not a news brief."""
    mine = {o.topic for o in obs if sg.start <= o.sec < brk.start and o.topic}
    ad = {o.topic for o in obs if brk.start <= o.sec < brk.start + BRIEF_AD_PROBE_SEC and o.topic}
    return bool(mine & ad)


def is_brief(sg: Segment) -> bool:
    """BRIEF-1: a short story that is complete on its own — an anchor read or a reporter's verbal
    add-on without a full package. It opened on a stable title (NEW-1) with at least one piece of
    change evidence (high confidence, not FB-1), carries news speech, and none of its titles is ad copy."""
    return (bool(sg.bar_texts) and sg.confidence == Confidence.HIGH.value and sg.fallback is None
            and "NEW-1" in sg.signals and bool(sg.asr_text)
            and not any(is_ad_copy(t) for t in sg.bar_texts))


# EVD-1: reason codes, not a made-up probability. Under the rules a boundary is decided by which
# evidence is present; a number such as 0.55 + 0.2 would look like a probability without being one
# (nobody calibrated it on held-out data). `score` is filled only by the learned model, and even
# then it is the model's raw output, labelled as such.
REASON_CODES = {
    "NEW-1": "HEADLINE_CHANGED", "NEW-2a": "TOPIC_SHIFT", "NEW-2b": "ANCHOR_SHOT", "NEW-2c": "SPEAKER_CHANGED",
    "NEW-2d": "NAME_CREDIT", "NEW-2e": "SPEECH_FOLLOWS_HEADLINE", "AD-7": "BACK_FROM_BREAK",
    "AD-8": "SAME_STORY_RESUMED", "AD-9": "BREAK_TIMEOUT", "SAME-8": "HEADLINE_RECURRED",
    "SAME-9": "SPEECH_TAIL_MERGED", "BRIEF-1": "SHORT_BRIEF", "FB-1": "TITLE_ONLY", "FB-3": "NO_TITLE_READ",
    "ML-new": "MODEL_NEW",
}


def _attach_boundary(sg: Segment, prev: Segment | None) -> None:
    """EVD-1/EVD-2: reason codes, evidence and review flag for the boundary at sg.start."""
    ids = set(sg.signals)
    after_break = prev is not None and prev.type in (EventType.AD, EventType.OUTAGE)
    if sg.type is not EventType.NEWS:
        kind = sg.type.value
    elif "AD-8" in ids:
        kind = "resume"  # same story back after the break (AD-8): no new story
    elif sg.boundary_p is not None:
        kind = "learned"
    elif sg.fallback == "FB-3" or "AD-9" in ids:
        kind = "speech"  # no title read: speech alone opened or named it
    else:
        kind = "after_break" if after_break else "title"
    sim = 0.0
    if sg.bar_texts and prev is not None and prev.bar_texts:
        sim = max(_ratio(a, b) for a in sg.bar_texts for b in prev.bar_texts)
    codes = [REASON_CODES[x] for x in dict.fromkeys(sg.signals) if x in REASON_CODES]
    if sg.fallback and REASON_CODES.get(sg.fallback) and REASON_CODES[sg.fallback] not in codes:
        codes.append(REASON_CODES[sg.fallback])
    if sg.overlay_sec:
        codes.append("AD_OVERLAY_TEXT_IGNORED")
    corroborated = bool({"NEW-2a", "NEW-2b", "NEW-2d", "NEW-2e", "AD-7", "AD-8"} & ids) or after_break
    sg.boundary = {
        "kind": kind,
        "score": sg.boundary_p,  # learned model's raw output; None under the rules (EVD-1)
        "score_source": "learned_model_uncalibrated" if sg.boundary_p is not None else None,
        "reason_codes": codes,
        "evidence": {
            "title_change": "NEW-1" in ids,
            "topic_shift": "NEW-2a" in ids,
            "anchor_shot": "NEW-2b" in ids,
            "speaker_change": "NEW-2c" in ids,
            "name_credit": "NEW-2d" in ids,
            "speech_follows_title": "NEW-2e" in ids,
            "title_similarity_prev": round(sim, 2),
            "after_break": after_break,
            "merged_recurrence": "SAME-8" in ids,
            "merged_speech_tail": "SAME-9" in ids,
            "brief": "BRIEF-1" in ids,
            "commercial_overlay_sec": sg.overlay_sec,
        },
        # 規則 EVD-2：低信心、備援、只有標題換了沒有佐證、短播報、出現過包框文字 → 需複查
        "review_required": sg.type is EventType.NEWS and kind != "resume" and (
            sg.confidence == Confidence.LOW.value or sg.fallback is not None
            or (kind == "title" and not corroborated) or "BRIEF-1" in ids or sg.overlay_sec > 0),
    }
