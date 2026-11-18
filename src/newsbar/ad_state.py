"""Ad / framed-ad state machine (spec §5, FB-5, FB-6).

Input: one channel's per-second observations. Output: a per-second state
("news" | "ad" | "outage") plus the residual bar text captured on entering an ad.
Only main-screen signals count; a corner ad never changes the state (AD-2).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .schemas import SecondObs

NEWS, AD, OUTAGE = "news", "ad", "outage"


@dataclass(frozen=True)
class AdParams:
    min_signals: int = 2  # AD-1
    min_duration_sec: int = 10  # AD-1
    return_confirm_sec: int = 5  # AD-7
    return_timeout_sec: int = 60  # AD-9
    outage_black_sec: int = 30  # FB-6
    merge_gap_sec: int = 8

    @classmethod
    def from_rules(cls, rules: dict | None) -> "AdParams":
        r = (rules or {}).get("ad_state", {})
        return cls(
            min_signals=r.get("min_signals", cls.min_signals),
            min_duration_sec=r.get("min_duration_sec", cls.min_duration_sec),
            return_confirm_sec=r.get("return_confirm_sec", cls.return_confirm_sec),
            return_timeout_sec=r.get("return_timeout_sec", cls.return_timeout_sec),
            outage_black_sec=r.get("outage_black_sec", cls.outage_black_sec),
        )


@dataclass
class AdBreak:
    start: int
    end: int  # exclusive: first news second
    residual_bar: str | None
    residual_topic: str | None
    signals: list[str] = field(default_factory=list)
    timed_out: bool = False  # AD-9


@dataclass
class AdResult:
    states: dict[int, str]
    breaks: list[AdBreak]


def _layout_back(o: SecondObs) -> bool:
    return not o.layout_changed and not o.bar_color_changed


def _return_ok(o: SecondObs, residual_bar: str | None, residual_topic: str | None, has_asr: bool = True) -> bool:
    """AD-7 (1)–(4) for one second.

    Without any ASR on the channel, (3) cannot be observed; the layout and bar
    colour must then carry the decision and any bar reading satisfies (4).
    """
    if not _layout_back(o):
        return False
    if not has_asr:
        return o.bar_text is not None
    speech = bool(o.asr_text) and not o.ad_speech  # (3) anchor/reporter speech
    if not speech:
        return False
    new_text = o.bar_text is not None and o.bar_text != residual_bar
    same_story_confirmed = (
        o.bar_text is not None
        and o.bar_text == residual_bar
        and residual_topic is not None
        and o.topic == residual_topic
    )
    return new_text or same_story_confirmed  # (4)


def carousel_seconds(obs: list[SecondObs], params) -> set[int]:
    """Seconds inside a headline carousel: within `carousel_window_sec`, at least
    `carousel_min_titles` different titles each shown ≤`carousel_max_sec`, and one of
    them coming back (11/06 FTV 12:36–12:38 cycled 10 teasers twice, 8–9 s each)."""
    runs: list[tuple[int, int, str]] = []
    prev, start = None, None
    for o in sorted(obs, key=lambda o: o.sec):
        t = o.bar_text
        if t != prev:
            if prev is not None:
                runs.append((start, o.sec - start, prev))  # type: ignore[arg-type]
            prev, start = t, o.sec
    if prev is not None:
        runs.append((start, obs[-1].sec - start + 1, prev))  # type: ignore[arg-type]
    titled = [r for r in runs if r[2] and params.stable_sec <= r[1] <= params.carousel_max_sec]
    out: set[int] = set()
    for i, (s0, _, _) in enumerate(titled):
        win = [r for r in titled[i:] if r[0] - s0 <= params.carousel_window_sec]
        texts = [r[2] for r in win]
        if len(set(texts)) >= params.carousel_min_titles and len(texts) > len(set(texts)):
            for r0, ln, _ in win:
                out.update(range(r0, r0 + ln))
    return out



class _CarouselParams:
    stable_sec = 7
    carousel_max_sec = 12
    carousel_window_sec = 120
    carousel_min_titles = 6


def detect(obs: list[SecondObs], params: AdParams = AdParams()) -> AdResult:
    obs = sorted(obs, key=lambda o: o.sec)
    n = len(obs)
    has_asr = any(o.asr_text for o in obs)
    # AD-g only means something on channels that normally show a ticker.
    has_ticker = sum(1 for o in obs if o.ticker_text) >= 0.5 * max(1, n)

    # AD-i: a headline-teaser carousel in the bar = framed ad (11/06 FTV ×3)
    carousel = carousel_seconds(obs, _CarouselParams())

    def sigs_of(o: SecondObs) -> list[str]:
        sg = o.ad_signals()
        if not has_ticker:
            sg = [x for x in sg if x != "AD-g"]
        if o.sec in carousel:
            sg = sg + ["AD-i"]
        return sg

    states: dict[int, str] = {o.sec: NEWS for o in obs}
    breaks: list[AdBreak] = []

    # FB-6: long black → outage (takes precedence over ad).
    i = 0
    while i < n:
        if obs[i].black:
            j = i
            while j < n and obs[j].black:
                j += 1
            if j - i > params.outage_black_sec:
                for k in range(i, j):
                    states[obs[k].sec] = OUTAGE
            i = j
        else:
            i += 1

    i = 0
    last_news: SecondObs | None = None
    while i < n:
        o = obs[i]
        if states[o.sec] == OUTAGE:
            i += 1
            continue
        # Look for a run where ≥min_signals hold for ≥min_duration (AD-1).
        if len(sigs_of(o)) >= 1:
            run_start = i  # first second any signal appears
            j = i
            strong = 0
            entered_at = None
            while j < n and sigs_of(obs[j]) and states[obs[j].sec] != OUTAGE:
                # The news ticker still on screen means the channel's overlay is live: a news
                # graphic can hide the logo and draw a frame edge (11/06 TVBS), so ask for one more.
                need = params.min_signals + (1 if obs[j].ticker_text else 0)
                sg = sigs_of(obs[j])
                score = len(sg) + (1 if "AD-i" in sg else 0)  # a teaser loop already sums 2 min of evidence
                strong = strong + 1 if score >= need else 0
                if strong >= params.min_duration_sec:
                    entered_at = j
                    break
                j += 1
            if entered_at is None:
                if last_news is None or o.bar_text is not None:
                    last_news = o
                i = j if j > i else i + 1
                continue

            strong_start = entered_at - params.min_duration_sec + 1
            run_start = max(run_start, strong_start - params.min_duration_sec)
            residual_bar = last_news.bar_text if last_news else None
            residual_topic = last_news.topic if last_news else None
            sigs: set[str] = set()
            # Inside the ad: find the return point (AD-7) or timeout (AD-9).
            k = entered_at
            ok_run = 0
            layout_back_since: int | None = None
            end = n
            timed_out = False
            while k < n:
                ok = obs[k]
                sigs.update(sigs_of(ok))
                if _layout_back(ok):
                    layout_back_since = k if layout_back_since is None else layout_back_since
                else:
                    layout_back_since = None
                if _return_ok(ok, residual_bar, residual_topic, has_asr) and "AD-i" not in sigs_of(ok):
                    ok_run += 1
                    if ok_run >= params.return_confirm_sec:
                        end = k - params.return_confirm_sec + 1
                        break
                else:
                    ok_run = 0
                if (
                    layout_back_since is not None
                    and k - layout_back_since + 1 > params.return_timeout_sec
                ):
                    end = layout_back_since + params.return_timeout_sec
                    timed_out = True
                    break
                k += 1
            for m in range(run_start, end):
                if states[obs[m].sec] != OUTAGE:
                    states[obs[m].sec] = AD
            breaks.append(
                AdBreak(
                    start=obs[run_start].sec,
                    end=obs[end].sec if end < n else obs[-1].sec + 1,
                    residual_bar=residual_bar,
                    residual_topic=residual_topic,
                    signals=sorted(sigs),
                    timed_out=timed_out,
                )
            )
            i = end
            last_news = None
            continue
        if o.bar_text is not None:
            last_news = o
        i += 1
    merged: list[AdBreak] = []
    for b in breaks:  # one break chopped by short gaps (11/06 CTS 12:47–12:48 came out in 6 pieces)
        if merged and b.start - merged[-1].end <= params.merge_gap_sec:
            for s in range(merged[-1].end, b.start):
                if states.get(s) == NEWS:
                    states[s] = AD
            merged[-1].end = b.end
            merged[-1].signals = sorted(set(merged[-1].signals) | set(b.signals))
        else:
            merged.append(b)
    return AdResult(states=states, breaks=merged)
