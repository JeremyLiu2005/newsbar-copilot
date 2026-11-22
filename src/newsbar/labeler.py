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
from . import words as wordseg

# Characters a label may add that are not content (aspect/voice particles).
FUNCTION_CHARS = set("中了再又仍已遭被將要的與和")

_PUNCT = re.compile(r"[\s「」『』（）()《》〈〉【】、，。！？：；…\"'·．.!?,:;]+")
_ASCII_WORD = re.compile(r"[A-Za-z0-9]+")
_CJK = re.compile(r"[一-鿿]")


@dataclass
class LabelSource:
    bar_texts: list[str] = field(default_factory=list)
    asr_text: str = ""
    feedback: str = ""  # 規則 §6.3：回饋重試，上一個答案錯在哪（字數、不在標題裡的字），只用在提示詞

    @property
    def text(self) -> str:
        return " ".join(self.bar_texts + ([self.asr_text] if self.asr_text else []))


class Generator(Protocol):
    def generate(self, source: LabelSource, attempt: int) -> str: ...


def label_length(label: str) -> int:
    """Visible characters (LBL-5, 三立 11/22：五字標只能 5 個字): every CJK character and every
    ASCII letter or digit counts 1, so 「泰山3100階」 is 7 characters, not 4."""
    return len(_CJK.findall(label)) + sum(len(w) for w in _ASCII_WORD.findall(label))


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
    min_len: int = 5  # 三立 11/22：五字標剛好 5 個字
    max_len: int = 5
    status_words: tuple[str, ...] = ("廣告", "氣象", "訊號中斷")
    sep: str = "/"

    @classmethod
    def from_rules(cls, rules: dict | None) -> "LabelRules":
        lab = (rules or {}).get("labeler") or {}
        return cls(min_len=int(lab.get("min_len", cls.min_len)), max_len=int(lab.get("max_len", cls.max_len)))


_TOKEN = re.compile(r"[A-Za-z0-9]|[^\sA-Za-z0-9]")


def enforce_max(label: str, rules: "LabelRules") -> str:
    """Hard cap (LBL-5): never let a label longer than max_len reach the sheet.
    Counts like label_length (each CJK char 1, each English/number word 1) and cuts at a unit
    boundary; trailing connectors (的 了 …) are dropped after the cut."""
    if label_length(label) <= rules.max_len:
        return label
    out, n = "", 0
    for m in _TOKEN.finditer(label):
        tok = m.group(0)
        unit = 1 if _CJK.fullmatch(tok) or tok.isalnum() else 0
        if n + unit > rules.max_len:
            break
        out += tok
        n += unit
    while out and _bad_end(out[-1]) and label_length(out) > 1:
        out = out[:-1]
    return out.strip() or label[: rules.max_len]


def _visible(text: str) -> str:
    return "".join(ch for ch in text if _CJK.fullmatch(ch) or (ch.isascii() and ch.isalnum()))


def pad_to_min(label: str, source: "LabelSource", rules: "LabelRules") -> str:
    """LBL-5 exact length: a fallback label shorter than min_len is replaced by an exact-length
    window of the title. Every window is scored by fuzzy overlap with the short label (keeps
    its focus), word boundaries at both ends (words.boundaries), places / event words, and penalised for a
    dangling connector or ASCII characters; every character still comes from the source (SRC-2)."""
    if label_length(label) >= rules.min_len:
        return label
    title = _visible(source.bar_texts[0] if source.bar_texts else source.asr_text)
    need = rules.min_len
    if len(title) < need:
        return label
    bounds = wordseg.boundaries(title)  # LBL-9: prefer windows that start and end on word boundaries
    lex = lexicon.get()
    core = set(_visible(label))
    best, best_score = None, None
    for st in range(len(title) - need + 1):
        w = title[st:st + need]
        score = 2 * (st in bounds) + 2 * (st + need in bounds) + 1.5 * sum(ch in core for ch in w)
        score += 6 * (bool(core) and _visible(label) in w)  # keeps the whole short label intact
        score += 1 if (lex.places_in(w) or any(k in w for k in lex.social_keywords)) else 0
        score -= 3 * (_bad_end(w[-1]) or _bad_start(w[0])) + sum(ch.isascii() for ch in w)
        if best_score is None or score > best_score:
            best, best_score = w, score
    return best or title[:need]


def feedback_for(cand: str, source: "LabelSource", errs: list[str], rules: "LabelRules") -> str:
    """One concrete sentence for the model: what exactly was wrong with its last answer."""
    parts = []
    n = label_length(cand)
    if "LBL-5" in errs:
        parts.append(f"「{cand}」是 {n} 個字，五字標必須剛好 {rules.max_len} 個字")
    if "SRC-2" in errs:
        src = set(" ".join(source.bar_texts)) if source.bar_texts else set(source.text)
        bad = "".join(dict.fromkeys(ch for ch in _CJK.findall(cand) if ch not in src and ch not in FUNCTION_CHARS))
        if bad:
            parts.append(f"「{bad}」不在主標題裡，不能用")
    if "LBL-4" in errs:
        parts.append("社會新聞要含主標題裡的地點")
    if "TONE-1" in errs:
        parts.append("要保留主標題裡「疑似、涉嫌、傳出」這類未確認的字")
    if "LBL-9" in errs:
        cut = cut_words(cand, source)
        if cut:
            parts.append("不要把詞切斷：" + "、".join(f"「{w}」" for w in cut) + " 要整個用或整個不用")
        orphan = orphan_units(cand, source)
        if orphan:
            parts.append("數字和單位要一起用或都不用：" + "、".join(f"「{w}」" for w in orphan))
    return "；".join(parts)


def cut_words(label: str, source: "LabelSource") -> list[str]:
    """LBL-9: the title words that the label cuts in half at its start or its end.

    Only the label's longest prefix and longest suffix that occur verbatim in a title are checked
    (≥2 characters each); a label assembled from separate pieces of the title is fine as long as
    its two ends sit on word boundaries (毒駕拒檢逃 ok; 規害婦撞亡 cuts 違規, 美股重挫元 cuts 元凶)."""
    out: list[str] = []
    for title in source.bar_texts:
        cuts: list[str] = []
        found = False
        for k in range(len(label), 1, -1):  # longest prefix found in the title
            pos = title.find(label[:k])
            if pos >= 0:
                found = True
                # the label's own first two characters being a word (起訴) overrides a doubtful split
                if pos not in wordseg.boundaries(title) and (wordseg.strong_word_at(title, pos)
                                                             or not wordseg.is_word(label[:2])):
                    cuts.append(_word_at(title, pos))
                break
        for k in range(len(label), 1, -1):  # longest suffix found in the title
            piece = label[-k:]
            pos = title.find(piece)
            if pos >= 0:
                found = True
                end = pos + len(piece)
                if end not in wordseg.boundaries(title) and (wordseg.strong_word_at(title, end)
                                                             or not wordseg.is_word(label[-2:])):
                    cuts.append(_word_at(title, end - 1))
                break
        if found and not cuts:
            return []  # one of the story's titles holds the label cleanly: that is enough
        out += cuts
    return list(dict.fromkeys(w for w in out if w))


_NUM_UNIT = re.compile(r"([0-9０-９]+)多?([萬億千百元人歲度點倍成年天週月名件公噸分號樓次輛家個票席])")


def orphan_units(label: str, source: "LabelSource") -> list[str]:
    """LBL-9: a counting word kept without its number (串聯60萬人 → 「串聯萬」, 交還7號球衣 → 「號球衣退出」).
    A number kept without its unit at the label's end is allowed (低溫探17, handbook example)."""
    out = []
    for title in source.bar_texts:
        for m in _NUM_UNIT.finditer(title):
            num, unit, whole = m.group(1), m.group(2), m.group(0)
            if whole in label or (num + unit) in label:
                continue
            i = label.find(unit)
            # the unit character may still belong to another word of the title (萬華, 號召): look for the
            # label's two-character windows around it in the title with this number taken out
            ctx = [label[k:k + 2] for k in (i - 1, i) if i >= 0 and 0 <= k and k + 2 <= len(label)]
            unit_alone = i >= 0 and num not in label and not any(
                c in t.replace(whole, "|") for c in ctx for t in source.bar_texts)
            if unit_alone:
                out.append(whole)
    return list(dict.fromkeys(out))


def best_window(label: str, source: "LabelSource", rules: "LabelRules") -> str:
    """LBL-9 last resort for the rule fallback: the exact-length window of a title that starts and
    ends on word boundaries, scored like pad_to_min (overlap with the rule label, places, event
    words, no connector at either end). Returns `label` unchanged when no aligned window exists."""
    need = rules.min_len
    lex = lexicon.get()
    core = set(_visible(label))
    best, best_score = None, None
    for raw in source.bar_texts:
        title = _visible(strip_lead_fragment(raw, rules))
        bs = sorted(wordseg.boundaries(title))
        for st, en in ((x, y) for x in bs for y in bs if y > x):
            w = title[st:en]
            if label_length(w) != need:
                continue
            score = 1.5 * sum(ch in core for ch in w) - 0.2 * st  # earlier = closer to the headline's subject
            score += 2 if (lex.places_in(w) or any(k in w for k in lex.social_keywords)) else 0
            score -= 3 * (_bad_end(w[-1]) or _bad_start(w[0])) + 2 * any(ch.isascii() for ch in w)
            if best_score is None or score > best_score:
                best, best_score = w, score
    return best or label


def _word_at(text: str, i: int) -> str:
    for s, e in wordseg.tokens(text):
        if s <= i < e:
            return text[s:e]
    return ""


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
    # 規則 LBL-5：長度剛好 5 個字（英文詞算 1 個字；上下限取自 labeler.min_len / max_len）
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
    # 規則 LBL-9：標籤的頭尾不可把主標題裡的詞切斷（違規→「規害…」、元凶→「…元」）
    if source.bar_texts and (cut_words(label, source) or orphan_units(label, source)):
        errors.append("LBL-9")
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
    out = _tidy(_tone_fit(_tidy(cand, rules), clean, place, rules), rules)
    # 規則 LBL-9：頭尾切到詞 → 改取主標題裡頭尾都落在詞界的字（找不到就保留，照常抽查）
    if source.bar_texts and (cut_words(out, source) or orphan_units(out, source)):
        win = best_window(out, source, rules)
        if not (cut_words(win, source) or orphan_units(win, source)):
            out = win
    return out


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


def repair_length(cand: str, source: LabelSource, rules: LabelRules) -> str | None:
    """LBL-10: an AI answer that only misses the exact length (4 or 6 characters is the usual 7B
    miss) is fixed here instead of being thrown away for the weaker rule label — but only when the
    fix cannot change what the answer says:

    too long  drop function characters / numbers, or whole words at either end of the answer
              (its own word boundaries), never a piece of a word (工會串聯罷工 → not 工會串聯工)
    too short the exact-length window of the title that contains the answer unchanged
              (海光歇業 is not in the title as one piece → no repair)
    The result must pass every check; otherwise None and the normal retry/fallback goes on."""
    n = label_length(cand)
    if n == rules.max_len or not (rules.max_len - 2 <= n <= rules.max_len + 2) or not all(_CJK.match(c) for c in cand):
        return None
    tries: list[str] = []
    if n > rules.max_len:
        extra = n - rules.max_len
        soft = [i for i, ch in enumerate(cand) if ch in END_BAD | START_BAD | FUNCTION_CHARS]
        from itertools import combinations
        for idxs in combinations(soft, extra):
            tries.append("".join(ch for i, ch in enumerate(cand) if i not in idxs))
        b = wordseg.boundaries(cand)
        for head in range(extra + 1):  # whole words off the front and/or the back
            tail = extra - head
            if head in b and len(cand) - tail in b:
                tries.append(cand[head:len(cand) - tail])
    else:
        win = pad_to_min(cand, source, rules)
        if cand in win:
            tries.append(win)
    for c in tries:
        if label_length(c) == rules.max_len and not validate(c, source, rules):
            return c
    return None


def repair_source(cand: str, source: LabelSource) -> list[str]:
    """LBL-10: answers whose characters outside the title line up with title characters (the model
    wrote Simplified: 台北兒虐严重 ↔ 台北兒虐嚴重, 蔡阿嘎员工 ↔ 蔡阿嘎前員工) rewritten with the title's own
    characters. For a replaced block the title piece of the same length is taken (from either end
    when the title piece is longer). Returns the possible rewrites; every character comes from the title."""
    if not source.bar_texts:
        return []
    from difflib import SequenceMatcher
    from itertools import product
    src = set(" ".join(source.bar_texts))
    outs: list[str] = []
    for title in source.bar_texts:
        sm = SequenceMatcher(None, cand, title, autojunk=False)
        if sm.ratio() < 0.4:
            continue
        parts: list[list[str]] = []
        changed = False
        for op, i1, i2, j1, j2 in sm.get_opcodes():
            piece = cand[i1:i2]
            if op == "replace" and any(_CJK.match(c) and c not in src for c in piece) and j2 - j1 >= i2 - i1:
                n = i2 - i1
                parts.append(list(dict.fromkeys([title[j2 - n:j2], title[j1:j1 + n]])))
                changed = True
            else:
                parts.append([piece])
        if changed:
            for combo in product(*parts):
                fixed = "".join(combo)
                if all(c in src or not _CJK.match(c) for c in fixed) and fixed not in outs:
                    outs.append(fixed)
    return outs


def repair(cand: str, source: LabelSource, rules: LabelRules) -> str | None:
    """LBL-10: Simplified → title characters, then exact length; None unless the result passes."""
    for c in repair_source(cand, source) or [cand]:
        errs = validate(c, source, rules)
        if not errs:
            if c != cand:
                return c
            continue
        if set(errs) == {"LBL-5"} and (fixed := repair_length(c, source, rules)):
            return fixed
    return None


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
            # 規則 LBL-10：寫成簡體字、或只差字數（多或少一兩個字）→ 直接修正再檢查，不浪費一次重試
            if fixed := repair(cand, source, rules):
                return LabelResult(fixed, attempt + 1, None, history, review_reasons(fixed, source))
        else:
            # 規則 §6.3：回饋重試，兩次都沒過，把「錯在哪」具體告訴 AI 再問最後一次
            fb = feedback_for(cand, source, errs, rules) if history else ""
            if fb:
                try:
                    cand = generator.generate(LabelSource(source.bar_texts, source.asr_text, fb), retries + 1)
                except Exception:  # noqa: BLE001
                    reasons.append(REASON_LLM_DOWN)
                else:
                    errs = validate(cand, source, rules)
                    history.append(errs)
                    if not errs:
                        return LabelResult(cand, len(history), None, history, review_reasons(cand, source))
                    if fixed := repair(cand, source, rules):
                        return LabelResult(fixed, len(history), None, history, review_reasons(fixed, source))
    # 規則 FB-4：兩次都沒通過、或 AI 沒回應 → 規則備援
    label = pad_to_min(enforce_max(rule_label(source, rules), rules), source, rules)
    # 規則 LBL-9：補足字數後又切到詞 → 改取主標題裡頭尾都落在詞界的 5 字
    if source.bar_texts and (cut_words(label, source) or orphan_units(label, source)):
        win = best_window(label, source, rules)
        if not (cut_words(win, source) or orphan_units(win, source)):
            label = win
    reasons += [r for r in review_reasons(label, source) if r not in reasons]
    return LabelResult(label, len(history), "FB-4", history, reasons)
