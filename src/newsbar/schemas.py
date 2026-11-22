"""Data contracts shared by every pipeline stage.

The minute sheet ("12監看") is the hardest constraint in the system: a blank
cell means "the previous state continues", so every stage upstream must hand
the mapper explicit state changes, never "unknown".
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


import re as _re

# Phone numbers and "back after the break" teasers only: news titles themselves carry
# 折/優惠/買1送1 (11/06 TVBS 旅展 story was misread as an ad).
_AD_COPY = _re.compile(r"0800|\d{2,4}-\d{3,4}-\d{3,4}|廣告後|立即回到|稍後回來|馬上回來|休息一下")


def is_ad_copy(text: str | None) -> bool:
    return bool(text) and bool(_AD_COPY.search(text))  # type: ignore[arg-type]


class EventType(str, Enum):
    NEWS = "news"
    AD = "ad"
    WEATHER = "weather"
    OUTAGE = "outage"


class ErrorType(str, Enum):
    """Shared by the review console and eval (see architecture §5)."""

    MISSED_CHANGE = "漏換題"
    SPURIOUS_CHANGE = "過度換題"
    FRAMED_AD_MISS = "包框誤判"
    CORNER_AD_FALSE = "角落廣告誤判"
    LABEL_UNRECOGNISABLE = "標不可辨認"
    FABRICATION = "捏造"
    FORMAT = "格式錯誤"


@dataclass(frozen=True)
class Labels:
    """Fixed strings written into the sheet (rules.yaml → output)."""

    ad: str = "廣告"
    weather: str = "氣象"
    outage: str = "訊號中斷"
    multi_sep: str = "/"

    @classmethod
    def from_rules(cls, rules: dict[str, Any] | None) -> "Labels":
        out = (rules or {}).get("output", {})
        return cls(
            ad=out.get("ad_label", cls.ad),
            weather=out.get("weather_label", cls.weather),
            outage=out.get("outage_label", cls.outage),
            multi_sep=out.get("multi_sep", cls.multi_sep),
        )


@dataclass
class Source:
    bar_text: str | None = None
    asr_text: str | None = None
    frame: str | None = None


@dataclass
class Event:
    """A state change on one channel.

    start_sec is relative to the sheet's first minute (12:00:00 → 0) and may be
    negative for a state that was already running when the sheet starts.
    story_id identifies one news story so that a repeated NEWS event for the
    same story is treated as continuation, not a new entry.
    """

    channel: str
    start_sec: float
    type: EventType
    label: str | None = None
    story_id: str | None = None
    confidence: str = "high"
    source: Source = field(default_factory=Source)
    signals: dict[str, Any] = field(default_factory=dict)
    fallback: str | None = None
    reasons: list[str] = field(default_factory=list)  # why a human should look at this cell (review queue only)

    def __post_init__(self) -> None:
        self.type = EventType(self.type)
        if isinstance(self.source, dict):
            self.source = Source(**self.source)
        if self.type is EventType.NEWS:
            if not self.label:
                raise ValueError(f"NEWS event on {self.channel} at {self.start_sec}s has no label")
            if not self.story_id:
                self.story_id = self.label

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["type"] = self.type.value
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Event":
        return cls(**d)


@dataclass
class SecondObs:
    """Everything the detectors know about one channel at one second.

    Upstream extractors (OCR, ASR, visual, fingerprint) fill this; the
    decision logic (ad_state, segmenter) reads only this, so it can be tested
    with synthetic streams.
    """

    sec: int
    bar_text: str | None = None  # majority-voted main_bar text; None = OCR failed
    bar_conf: float = 1.0
    ticker_text: str | None = None  # never used for decisions (TXT-2)
    weather_corner_text: str | None = None  # never used for decisions (TXT-2)
    live_tag: bool = False  # never used for decisions (BAN-1)
    corner_ad: bool = False  # never used for decisions (AD-2)
    layout_changed: bool = False  # AD-a
    bar_color_changed: bool = False  # AD-b
    logo_missing: bool = False  # AD-c
    black: bool = False  # AD-d
    ad_speech: bool = False  # AD-e
    fingerprint_hit: bool = False  # AD-f
    asr_text: str | None = None
    speaker: str | None = None
    topic: str | None = None  # ASR topic cluster id
    anchor_shot: bool = False  # camera on the anchor desk
    shot_cut: bool = False
    frame: str | None = None  # main_bar crop for evidence (FR-12)
    clock_ok: bool | None = None  # on-screen clock readable (None = not measured)

    def ad_signals(self) -> list[str]:
        names = {
            "AD-a": self.layout_changed,
            "AD-b": self.bar_color_changed,
            "AD-c": self.logo_missing,
            "AD-d": self.black,
            "AD-e": self.ad_speech,
            "AD-f": self.fingerprint_hit,
            # AD-g: the news ticker is gone — every channel keeps it on during news (11/06).
            "AD-g": self.ticker_text is None and self.frame is not None,
            # AD-h: the main-bar position shows ad copy or a "back after the break" teaser
            # (11/06 CTS 固力伸0800-030-031低至72折; FTV 「廣告後立即回到新聞現場」).
            "AD-h": is_ad_copy(self.bar_text),
            # AD-j: the channel clock is gone — every channel shows it throughout news,
            # a full-screen commercial covers it (11/06 TVBS 12:37:30–12:40:47).
            "AD-j": self.clock_ok is False,
        }
        return [k for k, v in names.items() if v]


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class Cell:
    """One written cell: channel × minute, with the events that produced it."""

    channel: str
    minute: str  # "HHMM"
    value: str
    events: list[Event]
