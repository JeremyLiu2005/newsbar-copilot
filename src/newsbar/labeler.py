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
    # 規則 LBL-5：標籤前後不可有空白
    if not label or label != label.strip():
        return ["LBL-5"]
    # 規則 OUT-grammar：不可含斜線，也不可等於「廣告／氣象／訊號中斷」
    if rules.sep in label or label in rules.status_words:
        errors.append("OUT-grammar")
    # 規則 LBL-5：長度 4–6 個字（英文詞算 1 個字）
    n = label_length(label)
    if not (rules.min_len <= n <= rules.max_len):
        errors.append("LBL-5")
    # 規則 SRC-2：每個中文字都要出自主標題（沒標題才用語音），只有少數助詞可額外加
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
    # 規則 LBL-4：社會新聞且來源有地點 → 標籤必須含地點，否則退件；來源沒地點不退件，只在 review_reasons 標抽查
    if is_social(basis):
        src_places = places_in(basis)
        if src_places and not any(label_has_place(p, label) for p in src_places):
            errors.append("LBL-4")
    # TONE-1 (R10): a hedged claim (疑似, 傳出, 恐將, 涉嫌 …) must stay hedged in the label.
    lex = lexicon.get()
    # 規則 TONE-1：只在來源有兩字以上的未確認詞、標籤把它拿掉時退件；單字只列入抽查（review_reasons）
    if lex.has_strong_hedge(hedge_basis(source)) and not lex.has_hedge(label):
        errors.append("TONE-1")
    return sorted(set(errors))


_CLAUSE = re.compile(r"[!?！？,，。.．:：;；、\s「」『』\"'()（）\[\]【】…]+")
# A short shout before ！ or ：  (大膽!…  驚傳:…) says nothing about the story.
LEAD_MAX_CHARS = 6  # 規則 LBL-8：驚嘆號或冒號前最多 6 字的開頭片語可以丟掉
_LEAD = re.compile(rf"^\s*([^!！:：\s]{{1,{LEAD_MAX_CHARS}}})\s*[!！:：]+\s*(\S.*)$", re.S)
TARGET_LEN = 5  # 五字標
_TARGET = TARGET_LEN
# 規則 LBL-7：標籤不可停在連接詞或助詞（臨停卸貨未、台股二度補…的）上，也不可以孤立數字開頭或結尾
END_BAD = set("未也的了被遭在與和及為是將已還更又就都才並且而或")
START_BAD = set("未也的了在與和及為是已還更又就都才並且而或")
_DIGITS = re.compile(r"\d+")
_NAME_DIGITS = re.compile(r"0\d{3,}")  # 0050 這類代號算名稱的一部分


def strip_lead_fragment(text: str, rules: "LabelRules | None" = None) -> str:
    """Drop a leading exclamation/colon fragment when what follows can carry a label alone."""
    rules = rules or LabelRules()
    m = _LEAD.match(text)
    if m and label_length(m.group(2)) >= rules.max_len:
        return m.group(2)
    return text


def _units(text: str, lex: "lexicon.Lexicon") -> list[str]:
    """Split into label units: an ASCII/digit run, a place name, a social keyword or a hedge
    marker is one unit (never cut in the middle), anything else is one Chinese character."""
    spans: dict[int, int] = {}
    for pos, surface, _ in lex.find_places(text):
        spans[pos] = len(surface)
    words = sorted({*lex.social_keywords, *(m for m in lex.hedge_markers if len(m) > 1)}, key=len, reverse=True)
    if words:
        for m in re.finditer("|".join(re.escape(w) for w in words), text):
            spans.setdefault(m.start(), len(m.group()))
    out: list[str] = []
    i = 0
    while i < len(text):
        if i in spans:
            out.append(text[i:i + spans[i]])
            i += spans[i]
            continue
        m = _ASCII_WORD.match(text, i)
        if m:
            out.append(m.group())
            i = m.end()
            continue
        if _CJK.match(text[i]):
            out.append(text[i])
        i += 1
    return out


# 規則 LBL-7：連接詞、助詞、單獨數字不可當開頭
def _bad_start(u: str) -> bool:
    if _DIGITS.fullmatch(u):
        return not _NAME_DIGITS.fullmatch(u)
    return u in START_BAD


# 規則 LBL-7：連接詞、助詞、單獨數字不可當結尾
def _bad_end(u: str) -> bool:
    if _DIGITS.fullmatch(u):
        return not _NAME_DIGITS.fullmatch(u)
    return u in END_BAD


def _ulen(units: list[str]) -> int:
    return sum(label_length(u) for u in units)


def _span(units: list[str], start: int, rules: "LabelRules") -> str | None:
    """From `start` take a min–max length span that does not end on a function/connector unit;
    prefer 5, on a tie the shorter one."""
    while start < len(units) and _bad_start(units[start]):
        start += 1
    best: tuple[float, str] | None = None
    for end in range(start + 1, len(units) + 1):
        n = _ulen(units[start:end])
        if n > rules.max_len:
            break
        if n >= rules.min_len and not _bad_end(units[end - 1]):
            cost = abs(n - _TARGET) - (1.5 if end == len(units) else 0)  # a span that closes the clause is a whole phrase
            if best is None or cost < best[0]:
                best = (cost, "".join(units[start:end]))
    return best[1] if best else None


def _anchored(units: list[str], k: int, prefix: str, rules: "LabelRules") -> str | None:
    """Label built around the event word at units[k]: optional place first, then the event word
    (a one-character word takes the unit after it), then following units up to 5; if that is
    still under the minimum, preceding units are added in front."""
    lo = k
    hi = k + 1
    if label_length(units[k]) == 1 and hi < len(units):
        hi += 1
    budget = rules.max_len - label_length(prefix)
    body = units[lo:hi]
    while hi < len(units) and _ulen(body) < _TARGET - label_length(prefix) and _ulen(body) + label_length(units[hi]) <= budget:
        body.append(units[hi])
        hi += 1
    while len(body) > 1 and _bad_end(body[-1]):
        body.pop()
    while lo > 0 and label_length(prefix) + _ulen(body) < rules.min_len and _ulen(body) + label_length(units[lo - 1]) <= budget:
        lo -= 1
        body.insert(0, units[lo])
    while len(body) > 1 and _bad_start(body[0]) and label_length(prefix) + _ulen(body[1:]) >= rules.min_len:
        body.pop(0)
    out = prefix + "".join(body)
    if label_length(out) < rules.min_len or _bad_end(body[-1]) or _bad_start(body[0]):
        return None
    return out


def _tidy(label: str, rules: "LabelRules") -> str:
    """Last safety net: never leave a connector or a lone number at the end."""
    out = label
    while label_length(out) > rules.min_len:
        m = re.search(r"(?<![A-Za-z])\d+$", out)
        if out[-1] in END_BAD:
            out = out[:-1]
        elif m and not _NAME_DIGITS.fullmatch(m.group()):
            out = out[: m.start()]
        else:
            break
    return out if label_length(out) >= rules.min_len else label


def _grow(prefix: str, tail: str, target: int, hard_max: int) -> str:
    """Append source tokens from `tail` until the label has `target` units (never above hard_max)."""
    out = prefix
    for tok in _units(tail, lexicon.get()):
        if label_length(out) >= target or label_length(out + tok) > hard_max:
            break
        out += tok
    return out


def _tone_fit(cand: str, clean: str, place: str | None, rules: "LabelRules") -> str:
    """The source hedges (疑似, 傳出, 恐 …) but the candidate dropped it: rebuild around the marker.
    Only a multi-character hedge is rebuilt around; a lone 疑/傳/恐/涉 is just flagged for review."""
    lex = lexicon.get()
    # 規則 TONE-1：只有兩字以上的未確認詞才圍繞它重建標籤
    hits = [h for h in lex.hedge_hits(clean) if len(h[1]) >= 2]
    if not hits or lex.has_hedge(cand):
        return cand
    pos, marker = hits[0]
    if place:
        # place + marker + whatever follows it in the source
        rebuilt = _grow(place + marker, clean[pos + len(marker):], rules.max_len, rules.max_len)
    else:
        start = max(0, pos - 2)  # two characters of subject before the marker
        rebuilt = _grow("", clean[start:], _TARGET, rules.max_len)
    rebuilt = _tidy(rebuilt, rules)
    return rebuilt if lex.has_hedge(rebuilt) and label_length(rebuilt) >= rules.min_len else cand


def rule_label(source: LabelSource, rules: LabelRules = LabelRules()) -> str:
    """FB-4: copy-only condensation, never ends in the middle of a word.
    The title is cut into clauses at punctuation/spaces (a leading exclamation fragment is
    dropped first). Social news → place + the clause's crime/accident words; otherwise the
    first clause that has an event word or a place (or fits whole in 4–6 characters), cut to a
    4–6 character span that does not end on a connector (未 也 的 了 …) nor start with a bare
    number. A hedge marker in the source is kept, every character comes from the source."""
    lex = lexicon.get()
    # 規則 FB-4、LBL-8：規則備援，先丟掉開頭的驚嘆片語，再依標點切短句
    text = strip_lead_fragment(source.bar_texts[0] if source.bar_texts else source.asr_text, rules)
    clean = _PUNCT.sub("", text)
    clauses = [c for c in _CLAUSE.split(text) if c]
    cand: str | None = None
    place: str | None = None
    kw_set = set(lex.social_keywords)
    # 規則 LBL-4、FB-4：社會新聞 → 以「地點＋事件詞」為核心擷取
    if is_social(clean):
        found = lex.find_places(clean)
        if found:
            _, surface, canon = found[-1]  # the most specific place, e.g. 土城 after 新北
            place = surface[:-1] if surface[-1] in "區鄉鎮市" and len(surface) > 2 else surface
            if not lex.place_in_label(canon, place):
                place = surface
        hits = []
        for ci, c in enumerate(clauses):
            us = _units(c, lex)
            ks = [i for i, u in enumerate(us) if u in kw_set]
            if ks:
                hits.append((0 if lex.places_in(c) else 1, ci, us, ks[0]))
        for _, _, us, k in sorted(hits, key=lambda t: (t[0], t[1])):
            built = _anchored(us, k, "", rules)
            if place and (not built or not any(lex.place_in_label(c, built) for c in lex.places_in(place))):
                built = _anchored(us, k, place, rules)  # the place may sit elsewhere in the clause: put it in front
            if built:
                cand = built
                break
    # 規則 FB-4：一般新聞 → 挑含地名或事件詞、或剛好 4–6 字的短句擷取
    if cand is None:
        scored = []
        for ci, c in enumerate(clauses):
            us = _units(c, lex)
            has_event = bool(lex.places_in(c)) or any(u in kw_set for u in us)
            digit_lead = bool(us) and _bad_start(us[0]) and bool(_DIGITS.fullmatch(us[0]))
            scored.append((not has_event, digit_lead, not (rules.min_len <= _ulen(us) <= rules.max_len), ci, us))
        for *_, us in sorted(scored, key=lambda t: t[:4]):
            start = 0
            pl = [i for i, u in enumerate(us) if u in lex.canon_of]
            if pl and _ulen(us) > rules.max_len:
                start = pl[0]  # a long clause with a place: start at the place
            cand = _span(us, start, rules)
            if cand:
                break
    if cand is None:
        cjk = "".join(_CJK.findall(clean))
        cand = (cjk or clean)[: rules.max_len] or "新聞"
    # 規則 TONE-1、LBL-7：保留兩字以上的未確認詞，且結尾不可是連接詞或單獨數字
    return _tidy(_tone_fit(_tidy(cand, rules), clean, place, rules), rules)


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
    # 規則 LBL-4：社會新聞標籤沒有地點 → 保留標籤，列入抽查「社會新聞無地點」
    if is_social(basis) and not any(lex.place_in_label(c, label) for c in lex.places_in(label)):
        out.append(REASON_NO_PLACE)
    # 規則 TONE-1：來源有未確認說法而標籤沒有 → 列入抽查「未確認語氣」（單字的只抽查）
    if lex.has_hedge(hedge_basis(source)) and not lex.has_hedge(label):
        out.append(REASON_TONE)
    return out


DEFAULT_RETRIES = 1  # 規則 §6.3：AI 標籤被退件後重試 1 次


def make_label(
    source: LabelSource,
    generator: Generator | None,
    retries: int = DEFAULT_RETRIES,
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
            # 規則 R20-2：AI 服務沒回應 → 不重試，直接改用規則備援並標記
            except Exception:  # noqa: BLE001 - uncertain cases must never stall the run
                reasons.append(REASON_LLM_DOWN)
                break
            errs = validate(cand, source, rules)
            history.append(errs)
            # 規則 §6.3：檢查通過就採用；沒通過就退件重試（共 1 次）
            if not errs:
                return LabelResult(cand, attempt + 1, None, history, review_reasons(cand, source))
    # 規則 FB-4：兩次都沒通過、或 AI 沒回應 → 規則備援
    label = rule_label(source, rules)
    reasons += [r for r in review_reasons(label, source) if r not in reasons]
    return LabelResult(label, len(history), "FB-4", history, reasons)
