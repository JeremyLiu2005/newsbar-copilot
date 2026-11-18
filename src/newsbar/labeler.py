"""Five-character label generation and validation (spec §6, FB-3, FB-4).

A Generator proposes a label (an LLM in production); the validator decides.
Nothing reaches the sheet unless it passes validation or comes from the rule
fallback, which only copies characters from the source.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol

# Characters a label may add that are not content (aspect/voice particles).
FUNCTION_CHARS = set("中了再又仍已遭被將要的與和")

SOCIAL_KEYWORDS = (
    "輾", "撞", "死", "亡", "傷", "砍", "殺", "毆", "火警", "火災", "墜", "溺",
    "竊", "偷", "搶", "詐", "毒駕", "酒駕", "車禍", "槍", "命案", "失蹤",
)

# Minimal place gazetteer; extend from a full Taiwan district list in production.
PLACES = (
    "台北", "新北", "桃園", "台中", "台南", "高雄", "基隆", "新竹", "苗栗", "彰化",
    "南投", "雲林", "嘉義", "屏東", "宜蘭", "花蓮", "台東", "澎湖", "金門", "馬祖",
    "土城", "板橋", "三重", "中和", "永和", "新莊", "新店", "樹林", "蘆洲", "汐止",
    "淡水", "信義", "大安", "中山", "萬華", "士林", "北投", "內湖", "南港", "文山",
    "中壢", "平鎮", "八德", "龜山", "竹北", "頭份", "豐原", "大里", "太平", "沙鹿",
    "鳳山", "左營", "三民", "苓雅", "前鎮", "楠梓", "岡山", "永康", "安平", "新營",
    "寧夏", "西門町",
)

_PUNCT = re.compile(r"[\s「」『』（）()《》〈〉【】、，。！？：；…\"'·．.!?,:;]+")
_ASCII_WORD = re.compile(r"[A-Za-z0-9]+")
_CJK = re.compile(r"[一-鿿]")


@dataclass
class LabelSource:
    bar_texts: list[str] = field(default_factory=list)
    asr_text: str = ""

    @property
    def text(self) -> str:
        return " ".join(self.bar_texts + ([self.asr_text] if self.asr_text else []))


class Generator(Protocol):
    def generate(self, source: LabelSource, attempt: int) -> str: ...


def label_length(label: str) -> int:
    """CJK characters count 1 each; an ASCII word counts 1 (LBL-5)."""
    return len(_CJK.findall(label)) + len(_ASCII_WORD.findall(label))


def is_social(text: str) -> bool:
    return any(k in text for k in SOCIAL_KEYWORDS)


def places_in(text: str) -> list[str]:
    found = [(text.find(p), p) for p in PLACES if p in text]
    return [p for _, p in sorted(found)]


@dataclass(frozen=True)
class LabelRules:
    min_len: int = 4
    max_len: int = 6
    status_words: tuple[str, ...] = ("廣告", "氣象", "訊號中斷")
    sep: str = "/"


def validate(label: str, source: LabelSource, rules: LabelRules = LabelRules()) -> list[str]:
    """Return the violated rule ids; empty list = accepted."""
    errors: list[str] = []
    src = source.text
    if not label or label != label.strip():
        return ["LBL-5"]
    if rules.sep in label or label in rules.status_words:
        errors.append("OUT-grammar")
    n = label_length(label)
    if not (rules.min_len <= n <= rules.max_len):
        errors.append("LBL-5")
    # SRC-2 / SRC-3 / LBL-2 / LBL-3: every content character must come from the source.
    src_chars = set(src)
    for ch in _CJK.findall(label):
        if ch not in src_chars and ch not in FUNCTION_CHARS:
            errors.append("SRC-2")
            break
    for word in _ASCII_WORD.findall(label):
        if word.lower() not in src.lower():
            errors.append("SRC-2")
            break
    # LBL-4: social news must carry a place that is in the source.
    if is_social(src):
        src_places = places_in(src)
        if src_places and not any(p in label for p in src_places):
            errors.append("LBL-4")
    return sorted(set(errors))


def rule_label(source: LabelSource, rules: LabelRules = LabelRules()) -> str:
    """FB-4: copy-only label = place + core verb, or the head of the bar text."""
    text = source.bar_texts[0] if source.bar_texts else source.asr_text
    clean = _PUNCT.sub("", text)
    places = places_in(clean)
    if is_social(clean) and places:
        place = places[-1]  # the most specific place, e.g. 土城 after 新北
        for kw in SOCIAL_KEYWORDS:
            pos = clean.find(kw)
            if pos >= 0:
                core = clean[pos : pos + 2]
                cand = (place + core)[: rules.max_len]
                if label_length(cand) >= rules.min_len:
                    return cand
                break
    cjk = "".join(_CJK.findall(clean))
    if len(cjk) >= rules.min_len:
        return cjk[:5]
    return (cjk or clean)[: rules.max_len] or "新聞"


@dataclass
class LabelResult:
    label: str
    attempts: int
    fallback: str | None
    errors: list[list[str]]


def make_label(
    source: LabelSource,
    generator: Generator | None,
    retries: int = 1,
    rules: LabelRules = LabelRules(),
) -> LabelResult:
    """§6.3: generate → validate → retry once → rule fallback."""
    history: list[list[str]] = []
    if generator is not None:
        for attempt in range(retries + 1):
            cand = generator.generate(source, attempt)
            errs = validate(cand, source, rules)
            history.append(errs)
            if not errs:
                return LabelResult(cand, attempt + 1, None, history)
    return LabelResult(rule_label(source, rules), len(history), "FB-4", history)
