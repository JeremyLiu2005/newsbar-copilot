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


def detect(obs: list[SecondObs], params: AdParams = AdParams()) -> AdResult:
    obs = sorted(obs, key=lambda o: o.sec)
    n = len(obs)
    has_asr = any(o.asr_text for o in obs)
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
        if len(o.ad_signals()) >= 1:
            run_start = i  # first second any signal appears
            j = i
            strong = 0
            entered_at = None
            while j < n and obs[j].ad_signals() and states[obs[j].sec] != OUTAGE:
                # The news ticker still on screen means the channel's overlay is live: a news
                # graphic can hide the logo and draw a frame edge (11/06 TVBS), so ask for one more.
                need = params.min_signals + (1 if obs[j].ticker_text else 0)
                strong = strong + 1 if len(obs[j].ad_signals()) >= need else 0
                if strong >= params.min_duration_sec:
                    entered_at = j
                    break
                j += 1
            if entered_at is None:
                if last_news is None or o.bar_text is not None:
                    last_news = o
                i = j if j > i else i + 1
                continue

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
                sigs.update(ok.ad_signals())
                if _layout_back(ok):
                    layout_back_since = k if layout_back_since is None else layout_back_since
                else:
                    layout_back_since = None
                if _return_ok(ok, residual_bar, residual_topic, has_asr):
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
    return AdResult(states=states, breaks=breaks)
