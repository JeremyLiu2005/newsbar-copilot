"""Five-character label generation and validation (spec §6, FB-3, FB-4).

A Generator proposes a label (an LLM in production); the validator decides.
Nothing reaches the sheet unless it passes validation or comes from the rule
fallback, which only copies characters from the source. A failing LLM never stops the
run: the call is caught and the rule label is used and flagged.
Place names, social-news keywords and hedge markers live in lexicon.py / config/lexicon.yaml.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol

from . import lexicon

# Characters a label may add that are not content (aspect/voice particles).
FUNCTION_CHARS = set("中了再又仍已遭被將要的與和")

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
    return any(k in text for k in lexicon.get().social_keywords)


def places_in(text: str) -> list[str]:
    """Canonical place names in text order (土城區 and 土城 both give 土城)."""
    return lexicon.get().places_in(text)


def label_has_place(canon: str, label: str) -> bool:
    return lexicon.get().place_in_label(canon, label)


def hedge_basis(source: "LabelSource") -> str:
    """Text whose hedge words the label must keep: the on-screen title when one was read."""
    return " ".join(source.bar_texts) if source.bar_texts else source.text


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
    # With a title on screen the label must come from the title itself; a long speech
    # transcript contains nearly every common character and would let anything pass.
    src_chars = set(" ".join(source.bar_texts)) if source.bar_texts else set(src)
    for ch in _CJK.findall(label):
        if ch not in src_chars and ch not in FUNCTION_CHARS:
            errors.append("SRC-2")
            break
    for word in _ASCII_WORD.findall(label):
        if word.lower() not in src.lower():
            errors.append("SRC-2")
            break
    # LBL-4: social news must carry a place that is in the source — the title when one was
    # read (a place mentioned only in speech, e.g. 前鎮, cannot be demanded of a title label).
    basis = " ".join(source.bar_texts) if source.bar_texts else src
    if is_social(basis):
        src_places = places_in(basis)
        if src_places and not any(label_has_place(p, label) for p in src_places):
            errors.append("LBL-4")
    # TONE-1 (R10): a hedged claim (疑似, 傳出, 恐, 涉嫌 …) must stay hedged in the label.
    lex = lexicon.get()
    if lex.has_hedge(hedge_basis(source)) and not lex.has_hedge(label):
        errors.append("TONE-1")
    return sorted(set(errors))


_CLAUSE = re.compile(r"[!?！？,，。:：;；、\s「」『』\"'()（）\[\]【】…]+")
_TOK = re.compile(r"[A-Za-z0-9]+|[一-鿿]")
# A short shout before ！ or ：  (大膽!…  驚傳:…) says nothing about the story.
_LEAD = re.compile(r"^\s*([^!！:：\s]{1,6})\s*[!！:：]+\s*(\S.*)$", re.S)
_TARGET = 5  # 五字標


def strip_lead_fragment(text: str, rules: "LabelRules | None" = None) -> str:
    """Drop a leading exclamation/colon fragment when what follows can carry a label alone."""
    rules = rules or LabelRules()
    m = _LEAD.match(text)
    if m and label_length(m.group(2)) >= rules.max_len:
        return m.group(2)
    return text


def _grow(prefix: str, tail: str, target: int, hard_max: int) -> str:
    """Append source tokens from `tail` until the label has `target` units (never above hard_max)."""
    out = prefix
    for tok in _TOK.findall(tail):
        if label_length(out) >= target or label_length(out + tok) > hard_max:
            break
        out += tok
    return out


def _cut(clause: str, n: int) -> str:
    out, units = "", 0
    for tok in _TOK.findall(clause):
        if units == n:
            break
        out += tok
        units += 1
    return out


def _tone_fit(cand: str, clean: str, place: str | None, rules: "LabelRules") -> str:
    """The source hedges (疑似, 傳出, 恐 …) but the candidate dropped it: rebuild around the marker."""
    lex = lexicon.get()
    hits = lex.hedge_hits(clean)
    if not hits or lex.has_hedge(cand):
        return cand
    pos, marker = hits[0]
    if place:
        # place + marker + whatever follows it in the source
        rebuilt = _grow(place + marker, clean[pos + len(marker):], rules.max_len, rules.max_len)
    else:
        start = max(0, pos - 2)  # two characters of subject before the marker
        rebuilt = _grow("", clean[start:], _TARGET, rules.max_len)
    return rebuilt if lex.has_hedge(rebuilt) and label_length(rebuilt) >= rules.min_len else cand


def rule_label(source: LabelSource, rules: LabelRules = LabelRules()) -> str:
    """FB-4: copy-only condensation. Social news → place + core event words; otherwise the
    first clause long enough. A leading exclamation fragment is dropped, a hedge marker in the
    source is kept, every character comes from the source, length 4–6 (digits kept: 月薪5萬)."""
    lex = lexicon.get()
    text = strip_lead_fragment(source.bar_texts[0] if source.bar_texts else source.asr_text, rules)
    clean = _PUNCT.sub("", text)
    cand: str | None = None
    place: str | None = None
    if is_social(clean):
        found = lex.find_places(clean)
        if found:
            _, surface, canon = found[-1]  # the most specific place, e.g. 土城 after 新北
            place = surface[:-1] if surface[-1] in "區鄉鎮市" and len(surface) > 2 else surface
            if not lex.place_in_label(canon, place):
                place = surface
        kws = [(clean.find(k), k) for k in lex.social_keywords if k in clean]
        if kws:
            pos, kw = min(kws, key=lambda t: (t[0], -len(t[1])))
            tail = clean[pos + len(kw):]
            core = kw if len(kw) > 1 else kw + (_TOK.findall(tail) or [""])[0]
            tail = tail[len(core) - len(kw):]
            built = _grow((place or "") + core, tail, _TARGET, rules.max_len)
            if label_length(built) >= rules.min_len:
                cand = built
    if cand is None:
        for clause in _CLAUSE.split(text):
            c = _cut(clause, _TARGET)
            if label_length(c) >= rules.min_len:
                cand = c
                break
    if cand is None:
        cjk = "".join(_CJK.findall(clean))
        cand = (cjk or clean)[: rules.max_len] or "新聞"
    return _tone_fit(cand, clean, place, rules)


@dataclass
class LabelResult:
    label: str
    attempts: int
    fallback: str | None
    errors: list[list[str]]
    reasons: list[str] = field(default_factory=list)  # why a human should look (shown in the review queue)


REASON_LLM_DOWN = "AI 服務無回應"
REASON_TONE = "未確認語氣"
REASON_NO_PLACE = "社會新聞無地點"


def review_reasons(label: str, source: LabelSource) -> list[str]:
    lex = lexicon.get()
    out = []
    basis = " ".join(source.bar_texts) if source.bar_texts else source.text
    if is_social(basis) and not any(lex.place_in_label(c, label) for c in lex.places_in(label)):
        out.append(REASON_NO_PLACE)
    if lex.has_hedge(hedge_basis(source)) and not lex.has_hedge(label):
        out.append(REASON_TONE)
    return out


def make_label(
    source: LabelSource,
    generator: Generator | None,
    retries: int = 1,
    rules: LabelRules = LabelRules(),
) -> LabelResult:
    """§6.3: generate → validate → retry once → rule fallback.

    An exception from the generator (Ollama down, timeout, bad JSON …) ends the attempts at once —
    retrying a dead service only waits for the next timeout — and the rule label is used with the
    reason 「AI 服務無回應」."""
    history: list[list[str]] = []
    reasons: list[str] = []
    if generator is not None:
        for attempt in range(retries + 1):
            try:
                cand = generator.generate(source, attempt)
            except Exception:  # noqa: BLE001 - uncertain cases must never stall the run
                reasons.append(REASON_LLM_DOWN)
                break
            errs = validate(cand, source, rules)
            history.append(errs)
            if not errs:
                return LabelResult(cand, attempt + 1, None, history, review_reasons(cand, source))
    label = rule_label(source, rules)
    reasons += [r for r in review_reasons(label, source) if r not in reasons]
    return LabelResult(label, len(history), "FB-4", history, reasons)
