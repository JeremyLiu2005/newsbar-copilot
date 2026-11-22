"""Turn per-channel state-change events into minute cells (architecture §4.10).

Two placement rules (Labels.minute_rule, rules.yaml output.minute_rule):
  * "next" (default, 三立 11/22): a change at hh:mm:ss with ss > 0 is written from
    the next minute; a cell holds one entry, the one on air when that minute starts
  * "floor" (10/26 example sheet): the minute it starts; several entries in one
    minute → joined with "/" in time order

Contract shared by both:
  * a new story → its label; continuation → blank
  * an ad break → "廣告" in its first minute, blank while it lasts
  * the first news minute after an ad or outage is always written, even when
    it is the same story as before (blank would read as "still in the ad")
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable

from .schemas import Cell, Event, EventType, Labels


class ContractError(ValueError):
    pass


def minute_labels(start: str = "1200", n_minutes: int = 60) -> list[str]:
    if len(start) != 4 or not start.isdigit():
        raise ContractError(f"start must be HHMM, got {start!r}")
    h, m = int(start[:2]), int(start[2:])
    out = []
    for i in range(n_minutes):
        total = h * 60 + m + i
        out.append(f"{(total // 60) % 24:02d}{total % 60:02d}")
    return out


def _state_key(ev: Event) -> tuple[EventType, str | None]:
    return (ev.type, ev.story_id if ev.type is EventType.NEWS else None)


def _text(ev: Event, labels: Labels) -> str:
    if ev.type is EventType.NEWS:
        return ev.label  # type: ignore[return-value]
    return {
        EventType.AD: labels.ad,
        EventType.WEATHER: labels.weather,
        EventType.OUTAGE: labels.outage,
    }[ev.type]


def validate_label(label: str, labels: Labels) -> None:
    if not label or not label.strip():
        # 規則 OUT-grammar：標籤不可空白、不可含分隔斜線、不可等於狀態字
        raise ContractError("empty label")
    if labels.multi_sep in label:
        raise ContractError(f"label {label!r} contains the separator {labels.multi_sep!r}")
    if label != label.strip():
        raise ContractError(f"label {label!r} has surrounding whitespace")
    if label in (labels.ad, labels.weather, labels.outage):
        raise ContractError(f"NEWS label {label!r} collides with a status word")


def map_events(
    events: Iterable[Event],
    channels: Iterable[str],
    start: str = "1200",
    n_minutes: int = 60,
    labels: Labels = Labels(),
    carry_in: bool = True,
    minute_rule: str | None = None,
) -> dict[str, dict[str, Cell]]:
    """Return {channel: {minute: Cell}} for every minute that gets a value.

    carry_in: a state already running before the first minute is written at the
    first minute so the sheet stands on its own (open question Q8).
    minute_rule: "next" | "floor"; None → labels.minute_rule.
    """
    rule = minute_rule or labels.minute_rule
    if rule not in ("next", "floor"):
        raise ContractError(f"minute_rule must be 'next' or 'floor', got {rule!r}")
    minutes = minute_labels(start, n_minutes)
    channels = list(channels)
    by_channel: dict[str, list[Event]] = defaultdict(list)
    for ev in events:
        if ev.channel not in channels:
            raise ContractError(f"event for unknown channel {ev.channel!r}")
        if ev.type is EventType.NEWS:
            validate_label(ev.label, labels)  # type: ignore[arg-type]
        by_channel[ev.channel].append(ev)

    result: dict[str, dict[str, Cell]] = {ch: {} for ch in channels}
    end_sec = n_minutes * 60
    for ch in channels:
        evs = sorted(by_channel[ch], key=lambda e: e.start_sec)
        state: tuple[EventType, str | None] | None = None
        pending: dict[int, list[Event]] = defaultdict(list)
        for ev in evs:
            # 規則 MAP-7：表格結束（13:00:00）之後的改變不寫入
            if ev.start_sec >= end_sec:
                break
            key = _state_key(ev)
            # 規則 MAP-5：連續相同狀態（同一則、或都是廣告）視為延續 → 留白（規則 OUT-1）
            if key == state:
                continue  # continuation → blank
            state = key
            # 規則 MAP-6：表格開始前就在進行的狀態，寫在第一格
            if ev.start_sec < 0:
                if not carry_in:
                    continue
                idx = 0
            elif rule == "next":
                # 規則 MAP-3（next）：不是整分 00 秒的換題，從下一分鐘開始寫；一格只放一則，
                # 同一格內後來的改變蓋掉先前的（那一分鐘開始時在播的才是這格的內容）
                idx = math.ceil(ev.start_sec / 60)
                if idx >= n_minutes:
                    continue
                pending[idx] = [ev]
                continue
            else:
                # 規則 MAP-3（floor）：秒數捨去到所在的那一分鐘
                idx = int(ev.start_sec // 60)
            pending[idx].append(ev)

        if rule == "next":
            # A cell overwritten back to the state already written above it is a continuation → blank.
            prev: tuple[EventType, str | None] | None = None
            for idx in sorted(pending):
                key = _state_key(pending[idx][-1])
                if key == prev:
                    del pending[idx]
                    continue
                prev = key

        for idx, cell_events in pending.items():
            # Entries carried in from before the sheet collapse to the latest one.
            pre = [e for e in cell_events if e.start_sec < 0]
            # 規則 MAP-6：開頭前的好幾個狀態只留最後一個
            if len(pre) > 1:
                cell_events = [pre[-1]] + [e for e in cell_events if e.start_sec >= 0]
            # 規則 OUT-2：同一分鐘有好幾次改變 → 依先後用「/」連起來；廣告寫「廣告」（OUT-4）、天氣寫「氣象」
            value = labels.multi_sep.join(_text(e, labels) for e in cell_events)
            result[ch][minutes[idx]] = Cell(ch, minutes[idx], value, cell_events)
    return result
