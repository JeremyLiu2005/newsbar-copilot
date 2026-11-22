"""Ad / framed-ad state machine (spec §5, FB-5, FB-6).

Input: one channel's per-second observations. Output: a per-second state
("news" | "ad" | "outage") plus the residual bar text captured on entering an ad.
Only main-screen signals count; a corner ad never changes the state (AD-2).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .schemas import SecondObs

NEWS, AD, OUTAGE = "news", "ad", "outage"
TICKER_EXTRA_SIGNALS = 1  # 跑馬燈還在畫面上時，進廣告要多一種訊號（規則 AD-1）


@dataclass(frozen=True)
class AdParams:
    min_signals: int = 2  # AD-1
    min_duration_sec: int = 10  # AD-1
    return_confirm_sec: int = 5  # AD-7
    return_timeout_sec: int = 60  # AD-9
    outage_black_sec: int = 30  # FB-6
    merge_gap_sec: int = 8
    min_break_sec: int = 25  # real breaks run ≥30 s; the 13 s programme intro at 12:00 is not one
    mode: str = "rules"  # "rules" (default) | "learned": a trained per-second probability replaces the signal count
    model: str | None = None  # learned: model file, or "active" (data/models/active.json)

    @classmethod
    def from_rules(cls, rules: dict | None) -> "AdParams":
        r = (rules or {}).get("ad_state", {})
        return cls(
            min_signals=r.get("min_signals", cls.min_signals),
            min_duration_sec=r.get("min_duration_sec", cls.min_duration_sec),
            return_confirm_sec=r.get("return_confirm_sec", cls.return_confirm_sec),
            return_timeout_sec=r.get("return_timeout_sec", cls.return_timeout_sec),
            outage_black_sec=r.get("outage_black_sec", cls.outage_black_sec),
            mode=r.get("mode", cls.mode),
            model=r.get("model", cls.model),
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


# 規則 AD-7（條件 1、2）：主畫面版面與標題條底色都恢復，這一秒才算「像新聞」
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
    # 規則 AD-7（條件 3）：要有主播或記者的講話，不是廣告詞
    speech = bool(o.asr_text) and not o.ad_speech  # (3) anchor/reporter speech
    if not speech:
        return False
    # 規則 AD-7（條件 4）：標題條出現與殘留標題不同的新標題，或同一則被語音確認
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
    # 規則 AD-i：120 秒內至少 6 個不同標題各停 7–12 秒且有重複 → 標題輪播（包框廣告的新聞預告）
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


AD_SIGNALS = [f"AD-{c}" for c in "abcdefghij"]
AD_FEATURES = AD_SIGNALS + ["ticker", "run_len"]


def signal_fn(obs: list[SecondObs]):
    """Per-second ad signals exactly as the rules read them (AD-g only on channels that normally
    show a ticker; AD-i = headline carousel). Shared by the rules and the learned mode."""
    n = len(obs)
    # AD-g only means something on channels that normally show a ticker.
    # 規則 AD-g：只有平常超過一半時間都有跑馬燈的頻道，「跑馬燈不見」才算廣告訊號
    has_ticker = sum(1 for o in obs if o.ticker_text) >= 0.5 * max(1, n)
    # AD-i: a headline-teaser carousel in the bar = framed ad (11/06 FTV ×3)
    # 規則 AD-i：輪播期間的每一秒多算一個廣告訊號
    carousel = carousel_seconds(obs, _CarouselParams())

    def sigs_of(o: SecondObs) -> list[str]:
        sg = o.ad_signals()
        if not has_ticker:
            sg = [x for x in sg if x != "AD-g"]
        if o.sec in carousel:
            sg = sg + ["AD-i"]
        return sg

    return sigs_of


def ad_feature_rows(obs: list[SecondObs]) -> list[tuple[int, dict]]:
    """(sec, features) for every second: the 10 signals, ticker on screen, and how many seconds in a
    row (up to now) any signal has been present."""
    obs = sorted(obs, key=lambda o: o.sec)
    sigs_of = signal_fn(obs)
    rows, run = [], 0
    for o in obs:
        sg = sigs_of(o)
        run = run + 1 if sg else 0
        f = {name: float(name in sg) for name in AD_SIGNALS}
        f["ticker"] = float(o.ticker_text is not None)
        f["run_len"] = float(min(run, 120))
        rows.append((o.sec, f))
    return rows


def detect(obs: list[SecondObs], params: AdParams = AdParams()) -> AdResult:
    obs = sorted(obs, key=lambda o: o.sec)
    n = len(obs)
    has_asr = any(o.asr_text for o in obs)
    sigs_of = signal_fn(obs)

    learned: list[bool] | None = None  # per second: the model says "ad"
    if params.mode == "learned":
        from .mlmodel import load_model

        if not params.model:
            raise ValueError("ad_state.mode: learned 需要 ad_state.model（模型檔路徑或 active）")
        mdl = load_model(params.model, "ad")
        rows = ad_feature_rows(obs)
        learned = [mdl.predict(f) >= mdl.threshold for _, f in rows]
    elif params.mode != "rules":
        raise ValueError(f"unknown ad_state.mode {params.mode!r}")

    states: dict[int, str] = {o.sec: NEWS for o in obs}
    breaks: list[AdBreak] = []

    # FB-6: long black → outage (takes precedence over ad).
    i = 0
    while i < n:
        # 規則 FB-6：黑畫面超過 30 秒 → 訊號中斷，優先於廣告判斷
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
        # 規則 AD-1：任兩種訊號持續 10 秒 → 進廣告（只看主畫面訊號，角落小廣告不算：規則 AD-2）
        # Look for a run where ≥min_signals hold for ≥min_duration (AD-1).
        if len(sigs_of(o)) >= 1:
            run_start = i  # first second any signal appears
            j = i
            strong = 0
            entered_at = None
            while j < n and sigs_of(obs[j]) and states[obs[j].sec] != OUTAGE:
                # The news ticker still on screen means the channel's overlay is live: a news
                # graphic can hide the logo and draw a frame edge (11/06 TVBS), so ask for one more.
                # 規則 AD-1：跑馬燈還在畫面上時，要多一種訊號才算
                need = params.min_signals + (TICKER_EXTRA_SIGNALS if obs[j].ticker_text else 0)
                sg = sigs_of(obs[j])
                score = len(sg) + (1 if "AD-i" in sg else 0)  # a teaser loop already sums 2 min of evidence
                hit = learned[j] if learned is not None else score >= need  # learned: p ≥ threshold replaces the count
                strong = strong + 1 if hit else 0
                if strong >= params.min_duration_sec:
                    entered_at = j
                    break
                j += 1
            if entered_at is None:
                # 規則 AD-OVL：包框廣告外框上的字不能當殘留標題
                if (last_news is None or o.bar_text is not None) and not o.commercial_overlay:
                    last_news = o
                i = j if j > i else i + 1
                continue

            strong_start = entered_at - params.min_duration_sec + 1
            run_start = max(run_start, strong_start - params.min_duration_sec)
            # 規則 AD-6：記下進廣告前最後一個標題，當作「殘留標題」
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
                still_ad = learned[k] if learned is not None else len(sigs_of(ok)) + ("AD-i" in sigs_of(ok)) >= params.min_signals
                # 規則 AD-7：回新聞條件連續成立 5 秒 → 廣告結束
                if _return_ok(ok, residual_bar, residual_topic, has_asr) and not still_ad:
                    ok_run += 1
                    if ok_run >= params.return_confirm_sec:
                        end = k - params.return_confirm_sec + 1
                        break
                else:
                    ok_run = 0
                if (
                    # 規則 AD-9：主畫面恢復後超過 60 秒仍判不出回新聞 → 強制結束廣告
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
        if o.bar_text is not None and not o.commercial_overlay:
            last_news = o
        i += 1
    merged: list[AdBreak] = []
    # 規則 AD-10：兩段廣告相隔 8 秒以內 → 併成一段
    for b in breaks:  # one break chopped by short gaps (11/06 CTS 12:47–12:48 came out in 6 pieces)
        if merged and b.start - merged[-1].end <= params.merge_gap_sec:
            for s in range(merged[-1].end, b.start):
                if states.get(s) == NEWS:
                    states[s] = AD
            merged[-1].end = b.end
            merged[-1].signals = sorted(set(merged[-1].signals) | set(b.signals))
        else:
            merged.append(b)
    kept = []
    for b in merged:
        # 規則 AD-11：整段不到 25 秒 → 不算廣告
        if b.end - b.start < params.min_break_sec:
            for s in range(b.start, b.end):
                if states.get(s) == AD:
                    states[s] = NEWS
        else:
            kept.append(b)
    return AdResult(states=states, breaks=kept)
