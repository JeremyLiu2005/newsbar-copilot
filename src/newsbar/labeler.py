"""新聞短標（俗稱五字標）generation and validation (spec §6, FB-3, FB-4).

Test version: 4–6 visible characters, about 5 preferred (labeler.min_len / max_len / target_len;
set min_len = max_len = 5 to go back to exactly five). Checks run in this order and the length
comes last: facts from the source → main action/result kept → complete words and numbers → length.

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
    min_len: int = 4  # 測試版 4–6 字（官方範例「高金求十四年」6 字；正式範圍待需求確認）
    max_len: int = 6
    target_len: int = 5  # 優先約 5 字
    status_words: tuple[str, ...] = ("廣告", "氣象", "訊號中斷")
    sep: str = "/"

    @classmethod
    def from_rules(cls, rules: dict | None) -> "LabelRules":
        lab = (rules or {}).get("labeler") or {}
        lo, hi = int(lab.get("min_len", cls.min_len)), int(lab.get("max_len", cls.max_len))
        return cls(min_len=lo, max_len=hi, target_len=min(hi, max(lo, int(lab.get("target_len", cls.target_len)))))


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
    return out.strip() or label  # never a blind title[:N] cut; the validator reports the length


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
    return best or label


def feedback_for(cand: str, source: "LabelSource", errs: list[str], rules: "LabelRules") -> str:
    """Concrete reasons for the rewrite request, in the validator's order (facts → result → words → length)."""
    parts = []
    if "SRC-2" in errs:
        src = set(" ".join(source.bar_texts)) if source.bar_texts else set(source.text)
        bad = "".join(dict.fromkeys(ch for ch in _CJK.findall(cand) if ch not in src and ch not in FUNCTION_CHARS))
        if bad:
            parts.append(f"「{bad}」不在主標題裡，不能新增")
    if "EVD-SRC" in errs:
        parts.append("evidence 必須逐字引用主標題或語音裡的片段")
    if "TONE-1" in errs:
        parts.append("要保留主標題裡「疑似、涉嫌、傳出」這類未確認的字")
    if "NEG-DROP" in errs:
        parts.append(f"來源說的是沒有「{negation_dropped(cand, source)}」，短標不可寫成有")
    if "EVT-MISS" in errs:
        parts.append(f"來源的主要結果是「{event_missing(cand, source)}」，短標漏掉了，要保留撞擊、死亡或受傷的結果")
    if "LBL-9" in errs:
        cut = cut_words(cand, source)
        if cut:
            parts.append("不要把詞切斷：" + "、".join(f"「{w}」" for w in cut) + " 要整個用或整個不用")
        orphan = orphan_units(cand, source)
        if orphan:
            parts.append("數字和單位要一起用或都不用：" + "、".join(f"「{w}」" for w in orphan))
    if "SEM-NUM" in errs:
        parts.append("數字被截斷：" + "、".join(f"「{w}」" for w in number_fragments(cand, source)) + " 要連單位一起用或都不用")
    if "SEM-OPEN" in errs:
        parts.append(f"句子沒說完（原文是「{open_ending(cand, source)}…」），要寫出完整的事件")
    if "LBL-5" in errs:
        parts.append(f"「{cand}」是 {label_length(cand)} 個字，要 {rules.min_len}–{rules.max_len} 個字（約 {rules.target_len} 字）")
    if "OUT-grammar" in errs:
        parts.append("不可含斜線，也不可只寫廣告／氣象／訊號中斷")
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


# 規則 EVT-1：來源的主要結果是傷亡時，短標要保留撞擊／死亡等結果（「堆高機警示」漏掉「害婦撞亡」）
CASUALTY_WORDS = ("撞死", "死亡", "身亡", "喪命", "不治", "致死", "溺斃", "命喪", "罹難", "死傷", "傷亡", "燒死", "摔死", "猝死")
_CASUALTY_DIE = re.compile(r"(?<![逃流存滅危])亡")  # 撞亡、輾亡、嬤亡 — not 逃亡 / 流亡 / 存亡
RESULT_CHARS = set("死治命撞輾燒殺傷墜溺斃")  # 亡 counts only through casualty_in (not 逃亡)
# 規則 SEM-2：句意未完。停在需要受詞的動詞上、而來源在它後面還有內容（法國再陷入｜動亂）
OPEN_VERBS = ("陷入", "進入", "遭到", "導致", "引發", "成為", "面臨", "宣布", "呼籲", "要求", "準備", "開始",
              "繼續", "出現", "發生", "展開", "爆發", "淪為", "涉及", "針對", "對於", "因為", "由於", "包括")
CN_NUM = set("一二三四五六七八九十兩百千萬億")
NUM_UNIT_CHARS = set("萬億千百元人歲度點倍成年天週月名件公噸分號樓次輛家個票席")
# a dropped unit the label's own words still carry (低溫探17 ← 17度: 「溫」 says it is degrees; 手冊範例)
UNIT_CARRIED_BY = {"度": "溫"}


_NEGATION = re.compile(r"(無|未|沒有|沒|免於|不致|並無|所幸無)[^，。!！?？,]{0,4}$")


def _negated(text: str, pos: int) -> bool:
    """「所幸無造成人員傷亡」: the casualty word at pos is negated by what stands just before it."""
    return bool(_NEGATION.search(text[max(0, pos - 8):pos]))


def casualty_in(text: str) -> str | None:
    for w in CASUALTY_WORDS:
        for m in re.finditer(re.escape(w), text):
            if not _negated(text, m.start()):
                return w
    for m in _CASUALTY_DIE.finditer(text):
        if not _negated(text, max(0, m.start() - 1)):  # 傷亡、撞亡: the word starts one character earlier
            return text[max(0, m.start() - 1):m.end()]
    return None


def negation_dropped(label: str, source: "LabelSource") -> str | None:
    """NEG-1 (facts): the label states a casualty / damage word that the source negates
    (所幸無造成人員傷亡 → 「造成人員傷亡」 says the opposite)."""
    words = [w for w in CASUALTY_WORDS + ("傷亡", "受傷", "死傷", "人員傷") if w in label]
    for t in source.bar_texts or [source.asr_text]:
        for w in words:
            for m in re.finditer(re.escape(w), t):
                if _negated(t, m.start()) and not re.search(r"(無|未|沒|不)", label):
                    return w
    return None


def event_missing(label: str, source: "LabelSource") -> str | None:
    """EVT-1: the source's main result is a death / injury but the label keeps neither the result nor
    the collision / fire / killing that caused it. Returns the result words the label dropped."""
    basis = source.bar_texts[0] if source.bar_texts else source.text  # the main title's result
    w = casualty_in(basis)
    if not w or any(ch in RESULT_CHARS for ch in label) or casualty_in(label):
        return None
    return w


def number_fragments(label: str, source: "LabelSource") -> list[str]:
    """SEM-1: a number cut off from its unit (拘提到案6 ← 「6人」). A number is complete when the label
    keeps it the way the title writes it (0050爆量, 3童): it is a fragment only when every place the
    title has this number it is followed by a counting unit and the label drops that unit."""
    out = []
    titles = source.bar_texts or [source.asr_text]
    for m in _DIGITS.finditer(label):
        num, after = m.group(), label[m.end():m.end() + 1]
        if after in NUM_UNIT_CHARS:
            continue
        # where the label's own text sits in a title (到案6｜人) decides; otherwise every use of the number
        ctx = label[max(0, m.start() - 2):m.end()]
        follows = [t[k.end():k.end() + 1] for t in titles for k in re.finditer(re.escape(ctx), t)]
        if not follows or ctx == num:
            follows = [t[k.end():k.end() + 1] for t in titles for k in re.finditer(re.escape(num) + r"(?!\d)", t)
                       if (k.start() == 0 or not t[k.start() - 1].isdigit())]
            if not (follows and all(f in NUM_UNIT_CHARS for f in follows)):
                continue
        elif not any(f in NUM_UNIT_CHARS for f in follows):
            continue
        unit = next(f for f in follows if f in NUM_UNIT_CHARS)
        if UNIT_CARRIED_BY.get(unit, "\0") in label:
            continue
        out.append(num + unit)
    return out


def open_ending(label: str, source: "LabelSource") -> str | None:
    """SEM-2: the label stops where the title's phrase is not finished — a Chinese numeral cut from
    what follows it (台股盤中一｜度), or a verb that needs its object (法國再陷入｜動亂)."""
    for t in source.bar_texts:
        v = _visible(t)
        for k in [m.start() for m in re.finditer(re.escape(label), v)]:
            nxt = v[k + len(label):k + len(label) + 1]
            if not nxt or not (_CJK.match(nxt) or nxt.isdigit()):
                continue
            if label[-1] in CN_NUM:  # 台股盤中一｜度、立委十｜…: a numeral never ends a finished phrase
                return label[-1] + nxt
            if label[-1] in END_BAD:  # 巴黎鐵塔又｜關: a connector / adverb waiting for its verb
                return label[-1] + nxt
            if label[-1] in "撞輾燒墜砍" and nxt in "亡死傷毀爛飛":  # 害婦撞｜亡: the result cut off
                return label[-1] + nxt
            end = k + len(label)
            last = next(((a, b) for a, b in _token_spans(v) if a < end <= b), None)
            # a one-character last word whose quantity follows (變電詐園買｜一送一)
            if last and last[1] - last[0] == 1 and (nxt in CN_NUM or nxt.isdigit()):
                j = end
                while j < len(v) and (v[j] in CN_NUM or v[j].isdigit()):
                    j += 1
                if j >= len(v) or v[j] not in NUM_UNIT_CHARS:  # 到案｜6人 is a new clause, 買｜一送一 is not
                    return label[-1] + v[end:j + 1]
            for vb in OPEN_VERBS:
                if label.endswith(vb):
                    return vb + nxt
    return None


def validate(label: str, source: LabelSource, rules: LabelRules = LabelRules()) -> list[str]:
    """Return the violated rule ids; empty list = accepted.

    Order (the length is checked last): a. facts come from the source (SRC-2, TONE-1) → b. main
    action / result kept (EVT-1) → c. complete words and numbers, finished phrase (LBL-9, SEM-1,
    SEM-2) → d. length and format (LBL-5, OUT-grammar). A missing place is no longer an error
    (LBL-4 is a review hint now, see review_notes)."""
    errors: list[str] = []
    src = source.text
    if not label or label != label.strip():
        return ["LBL-5"]
    # a. 規則 SRC-2：每個中文字都要出自主標題（沒標題才用語音），只有少數助詞可額外加
    src_chars = set(" ".join(source.bar_texts)) if source.bar_texts else set(src)
    for ch in _CJK.findall(label):
        if ch not in src_chars and ch not in FUNCTION_CHARS:
            errors.append("SRC-2")
            break
    for word in _ASCII_WORD.findall(label):
        if word.lower() not in src.lower():
            errors.append("SRC-2")
            break
    lex = lexicon.get()
    # 規則 TONE-1：只在來源有兩字以上的未確認詞、標籤把它拿掉時退件；單字只列入抽查
    if lex.has_strong_hedge(hedge_basis(source)) and not lex.has_hedge(label):
        errors.append("TONE-1")
    # 規則 NEG-1：來源否定的傷亡不可寫成發生（所幸無造成人員傷亡）
    if negation_dropped(label, source):
        errors.append("NEG-DROP")
    # b. 規則 EVT-1：主要結果（傷亡）不可漏
    if event_missing(label, source):
        errors.append("EVT-MISS")
    # c. 規則 LBL-9 / SEM-1 / SEM-2：不斷詞、不留數字殘片、句意要完整
    if source.bar_texts and (cut_words(label, source) or orphan_units(label, source)):
        errors.append("LBL-9")
    if number_fragments(label, source):
        errors.append("SEM-NUM")
    if open_ending(label, source):
        errors.append("SEM-OPEN")
    # d. 規則 LBL-5 / OUT-grammar：字數範圍（labeler.min_len / max_len）、不可含斜線或狀態字
    if rules.sep in label or label in rules.status_words:
        errors.append("OUT-grammar")
    n = label_length(label)
    if not (rules.min_len <= n <= rules.max_len):
        errors.append("LBL-5")
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
    if cand is None:  # no clause gives a span: keep the shortest whole clause (never a title[:N] cut)
        cand = min((c for c in clauses if _CJK.search(c)), key=label_length, default=clean) or clean
    # 規則 TONE-1、LBL-7：保留兩字以上的未確認詞，且結尾不可是連接詞或單獨數字
    out = _tidy(_tone_fit(_tidy(cand, rules), clean, place, rules), rules)
    # 規則 LBL-9：頭尾切到詞 → 改取主標題裡頭尾都落在詞界的字（找不到就保留，照常抽查）
    if source.bar_texts and (cut_words(out, source) or orphan_units(out, source)):
        win = best_window(out, source, rules)
        if not (cut_words(win, source) or orphan_units(win, source)):
            out = win
    return out


# 規則 REV-1：複查分三級（寫進 evidence 的 review_level）。只有「建議回查」與「處理失敗」標黃。
LEVEL_NONE = "無"
LEVEL_INFO = "資訊提示"  # 使用備援、短標未含地點…：留紀錄，不列入複查清單、不標黃
LEVEL_CHECK = "建議回查"  # 人名不穩定、疑似斷詞、可能漏掉主要結果：附證據與具體原因，標黃
LEVEL_FAIL = "處理失敗"  # 來源讀不到、時段缺漏、輸出不完整
LEVEL_ORDER = {LEVEL_NONE: 0, LEVEL_INFO: 1, LEVEL_CHECK: 2, LEVEL_FAIL: 3}
STATUS_OK = "合格"
STATUS_PENDING = "待確認"  # no candidate passed every check: the best one is kept and flagged


def top_level(notes: list[dict]) -> str:
    return max((n["level"] for n in notes), key=LEVEL_ORDER.get, default=LEVEL_NONE)


@dataclass
class LabelResult:
    label: str
    attempts: int
    fallback: str | None
    errors: list[list[str]]
    reasons: list[str] = field(default_factory=list)  # 建議回查／處理失敗 reasons (shown in the review queue)
    notes: list[dict] = field(default_factory=list)  # every note with its level (資訊提示 included)
    status: str = STATUS_OK
    evidence: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)

    @property
    def history(self) -> list[list[str]]:
        return self.errors


REASON_LLM_DOWN = "AI 服務無回應"
REASON_TONE = "未確認語氣"
REASON_NO_PLACE = "短標未含地點"
REASON_FALLBACK = "使用規則備援生成"
REASON_PENDING = "無法產生合格短標，保留最佳候選"


def _note(level: str, reason: str, **extra) -> dict:
    return {"level": level, "reason": reason, **extra}


def _hedge_scope_hit(label: str, basis: str) -> bool:
    """The label states what a hedge in the source qualifies (疑｜縱火 and the label has 縱火)."""
    lex = lexicon.get()
    for pos, marker in lex.hedge_hits(basis):
        scope = basis[pos + len(marker):pos + len(marker) + 4]
        if sum(ch in label for ch in set(scope) if _CJK.match(ch)) >= 2:
            return True
    return False


def review_notes(label: str, source: LabelSource) -> list[dict]:
    """REV-1 notes for an accepted label. A missing place is only a hint: social news is limited
    to crime / accident / disaster read from the main title, and a place is never invented."""
    lex = lexicon.get()
    out: list[dict] = []
    primary = source.bar_texts[0] if source.bar_texts else source.text
    if is_social(primary) and not any(lex.place_in_label(c, label) for c in lex.places_in(label)):
        places = lex.places_in(" ".join(source.bar_texts) or source.asr_text)
        hint = f"（來源有地點：{'、'.join(dict.fromkeys(places))}）" if places else "（來源沒有可靠地點，不補造）"
        out.append(_note(LEVEL_INFO, REASON_NO_PLACE, detail=hint))
    basis = hedge_basis(source)
    if lex.has_hedge(basis) and not lex.has_hedge(label):
        level = LEVEL_CHECK if _hedge_scope_hit(label, basis) else LEVEL_INFO
        out.append(_note(level, REASON_TONE, detail="短標寫成定論" if level == LEVEL_CHECK else "未確認詞不在短標內容上"))
    return out


def review_reasons(label: str, source: LabelSource) -> list[str]:
    """Every note's reason (資訊提示 included); see review_notes for the levels."""
    return [n["reason"] for n in review_notes(label, source)]


DEFAULT_RETRIES = 2  # 規則 §6.3：失敗時帶具體原因重寫，最多 2 次


# ---- model answer: JSON {short_title, evidence, issues}; plain text still accepted ---------------

def parse_answer(text: str) -> tuple[str, list[str], list[str], bool]:
    """(short_title, evidence, issues, was_json)."""
    import json as _json
    raw = (text or "").strip()
    m = re.search(r"\{.*\}", raw, re.S)
    if m:
        try:
            d = _json.loads(m.group())
        except ValueError:
            d = None
        if isinstance(d, dict) and "short_title" in d:
            ev = d.get("evidence") or []
            iss = d.get("issues") or []
            ev = [ev] if isinstance(ev, str) else [str(x) for x in ev]
            iss = [iss] if isinstance(iss, str) else [str(x) for x in iss]
            return str(d.get("short_title") or "").strip(" 「」『』\"'。．."), ev, [x for x in iss if x.strip()], True
    line = raw.splitlines()[0] if raw else ""
    line = re.sub(r"^(五字標|短標|新聞短標|答案)[:：]\s*", "", line)
    return line.strip(" 「」『』\"'。．."), [], [], False


def evidence_ok(evidence: list[str], source: LabelSource) -> bool:
    """EVD-SRC: every evidence piece the model cites must be a (near-)verbatim piece of the title or
    the speech — the program checks it, the model's claim alone is not trusted."""
    from difflib import SequenceMatcher
    if not evidence:
        return False
    texts = [t for t in source.bar_texts + [source.asr_text] if t]
    for e in evidence:
        e = _visible(e)
        if len(e) < 2:
            return False
        ok = False
        for t in texts:
            v = _visible(t)
            if e in v:
                ok = True
                break
            n = len(e)
            for i in range(0, max(1, len(v) - n + 1)):
                if SequenceMatcher(None, e, v[i:i + n]).ratio() >= 0.8:
                    ok = True
                    break
            if ok:
                break
        if not ok:
            return False
    return True


# ---- rule fallback: whole event phrases from the title's own words (never title[:N]) ------------

def _token_spans(title: str) -> list[tuple[int, int]]:
    spans = list(wordseg.tokens(title)) or [(i, i + 1) for i in range(len(title))]
    return [(a, b) for a, b in spans if b > a]


def _score(cand: str, title_idx: int, start: int, rules: LabelRules, clause: str = "", end: int | None = None
           ) -> float:
    lex = lexicon.get()
    sc = -0.6 * abs(label_length(cand) - rules.target_len) - 0.03 * start - 2.5 * title_idx  # the main title first
    if clause and end is not None:
        sc += 1.0 if start == 0 else 0  # keeps the clause's subject
        sc += 1.0 if end == len(clause) else 0  # closes the clause: a whole phrase
        sc -= 3 if wordseg.cuts_word(clause, start, end) else 0
    sc += 3 if casualty_in(cand) or any(ch in RESULT_CHARS for ch in cand) else 0
    sc += 1.5 if any(k in cand for k in lex.social_keywords) else 0
    sc += 1.5 if lex.places_in(cand) else 0
    sc -= 2 if cand[0] in START_BAD | set("遭被將再") else 0
    sc -= 1.5 * sum(ch.isascii() and ch.isdigit() for ch in cand) / max(1, len(cand))
    return sc


def fallback_candidates(source: LabelSource, rules: LabelRules = LabelRules(), avoid: set[str] = frozenset()
                        ) -> list[tuple[float, str]]:
    """Candidate labels built from whole words of the titles: contiguous word runs, and a subject
    word plus a later event phrase (法國｜動亂, 堆高機｜撞死). `avoid` = OCR-doubtful pieces that must
    not be copied. Returned best first with their score."""
    out: dict[str, float] = {}
    titles = source.bar_texts or ([source.asr_text] if source.asr_text else [])
    for ti, raw in enumerate(titles[:4]):
        for clause in _CLAUSE.split(strip_lead_fragment(raw, rules)):
            v = _visible(clause)
            if not v:
                continue
            sp = _token_spans(v)
            n = len(sp)
            runs = []
            for i in range(n):
                for j in range(i, min(n, i + 6)):
                    a, b = sp[i][0], sp[j][1]
                    w = v[a:b]
                    if label_length(w) > rules.max_len + 2:
                        break
                    # a one-character word at a cut edge usually belongs to its neighbour (男慘…, …縱火燒｜警車)
                    edge = 1.5 * ((sp[i][1] - sp[i][0] == 1 and a != 0) + (sp[j][1] - sp[j][0] == 1 and b != len(v)))
                    runs.append((a, b, w, edge))
            for a, b, w, edge in runs:
                if rules.min_len <= label_length(w) <= rules.max_len:
                    out[w] = max(out.get(w, -99), _score(w, ti, a, rules, v, b) + 0.5 - edge)
            runs = [r[:3] for r in runs]
            for a1, b1, w1 in runs:
                # subject at the clause start (or a place) + a later event phrase only
                if label_length(w1) < 2 or label_length(w1) > 3 or not (a1 == 0 or lexicon.get().places_in(w1)):
                    continue
                for a2, b2, w2 in runs:
                    if a2 <= b1 or a2 - b1 > 4 or label_length(w2) < 2:  # far-apart pieces change the meaning
                        continue
                    w = w1 + w2
                    if not (rules.min_len <= label_length(w) <= rules.max_len):
                        continue
                    if not (casualty_in(w2) or any(ch in RESULT_CHARS for ch in w2)
                            or any(k in w2 for k in lexicon.get().social_keywords) or lexicon.get().places_in(w1)
                            or wordseg.is_word(w2)):
                        continue
                    sc = _score(w, ti, a1, rules) - 0.8 + (1.0 if a1 == 0 else 0) + (1.0 if b2 == len(v) else 0)
                    sc -= 3 if wordseg.cuts_word(v, a1, b1) or wordseg.cuts_word(v, a2, b2) else 0
                    out[w] = max(out.get(w, -99), sc)
        # crime / accident news: the title's (most specific) place + an event phrase from any clause
        lex = lexicon.get()
        found = lex.find_places(raw) if is_social(raw) else []
        if found:
            _, surface, canon = found[-1]
            place = surface[:-1] if surface[-1] in "區鄉鎮市" and len(surface) > 2 else surface
            if not lex.place_in_label(canon, place):
                place = surface
            for clause in _CLAUSE.split(strip_lead_fragment(raw, rules)):
                v = _visible(clause)
                sp = _token_spans(v)
                for i in range(len(sp)):
                    for j in range(i, min(len(sp), i + 4)):
                        w2 = v[sp[i][0]:sp[j][1]]
                        if place in w2 or not (casualty_in(w2) or any(k in w2 for k in lex.social_keywords)):
                            continue
                        w = place + w2
                        if rules.min_len <= label_length(w) <= rules.max_len and not wordseg.cuts_word(v, sp[i][0], sp[j][1]):
                            out[w] = max(out.get(w, -99), _score(w, ti, 0, rules) + (1.0 if sp[j][1] == len(v) else 0))
    cands = [(sc, w) for w, sc in out.items() if not any(x and x in w for x in avoid)]
    return sorted(cands, key=lambda t: -t[0])


def fallback_label(source: LabelSource, rules: LabelRules = LabelRules(), avoid: set[str] = frozenset()
                   ) -> tuple[str, list[str]]:
    """FB-4: the best candidate that passes every check → (label, []); none passes → the candidate
    with the fewest problems and its failed checks (the caller marks it 待確認)."""
    cands = fallback_candidates(source, rules, avoid)
    legacy = rule_label(source, rules)
    if legacy and not any(x and x in legacy for x in avoid):
        cands.append((-1.0, legacy))
    best_bad: tuple[int, float, str, list[str]] | None = None
    for sc, w in cands:
        errs = validate(w, source, rules)
        if not errs:
            return w, []
        key = (len(errs), -sc)
        if best_bad is None or key < best_bad[:2]:
            best_bad = (len(errs), -sc, w, errs)
    if best_bad:
        return best_bad[2], best_bad[3]
    whole = _visible(strip_lead_fragment(source.bar_texts[0] if source.bar_texts else source.asr_text, rules))
    return whole or "新聞", ["LBL-5"]


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
    if rules.min_len <= n <= rules.max_len or not (rules.min_len - 2 <= n <= rules.max_len + 2) \
            or not all(_CJK.match(c) for c in cand):
        return None
    tries: list[str] = []
    if n > rules.max_len:
        from itertools import combinations
        soft = [i for i, ch in enumerate(cand) if ch in END_BAD | START_BAD | FUNCTION_CHARS]
        b = wordseg.boundaries(cand)
        for extra in range(n - rules.max_len, n - rules.min_len + 1):  # any length inside the range
            for idxs in combinations(soft, extra):
                tries.append("".join(ch for i, ch in enumerate(cand) if i not in idxs))
            for head in range(extra + 1):  # whole words off the front and/or the back
                tail = extra - head
                if head in b and len(cand) - tail in b:
                    tries.append(cand[head:len(cand) - tail])
    else:
        win = pad_to_min(cand, source, rules)
        if cand in win:
            tries.append(win)
    for c in tries:
        if rules.min_len <= label_length(c) <= rules.max_len and not validate(c, source, rules):
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
    avoid: set[str] = frozenset(),
) -> LabelResult:
    """§6.3: generate → check (facts, result, complete words, length) → rewrite with the concrete
    reasons, at most `retries` times → word-aware rule fallback. A fallback that still fails a check
    is kept as the best candidate with status 待確認 and a 建議回查 note (never silently passed).

    An exception from the generator (Ollama down, timeout, bad JSON …) ends the attempts at once
    and the rule fallback is used with the note 「AI 服務無回應」."""
    history: list[list[str]] = []
    notes: list[dict] = []
    fb = ""
    if generator is not None:
        for attempt in range(retries + 1):
            try:
                raw = generator.generate(LabelSource(source.bar_texts, source.asr_text, fb), attempt)
            # 規則 R20-2：AI 服務沒回應 → 不重試，直接改用規則備援並標記
            except Exception:  # noqa: BLE001 - uncertain cases must never stall the run
                notes.append(_note(LEVEL_INFO, REASON_LLM_DOWN))
                break
            cand, evid, issues, was_json = parse_answer(raw)
            errs = validate(cand, source, rules)
            if was_json and not evidence_ok(evid, source):
                errs = sorted(set(errs) | {"EVD-SRC"})
            if any(x and x in cand for x in avoid):
                errs = sorted(set(errs) | {"OCR-DOUBT"})
            history.append(errs)
            if not errs or (set(errs) <= {"LBL-5"} and (fixed := repair(cand, source, rules))
                            and not any(x and x in fixed for x in avoid)):
                label = cand if not errs else fixed
                n = review_notes(label, source) + [_note(LEVEL_CHECK, "模型回報：" + i) for i in issues]
                return _finish(LabelResult(label, attempt + 1, None, history, [], notes + n, STATUS_OK, evid, issues))
            if fixed := (repair(cand, source, rules) if "SRC-2" in errs else None):
                if not any(x and x in fixed for x in avoid):
                    n = review_notes(fixed, source) + [_note(LEVEL_CHECK, "模型回報：" + i) for i in issues]
                    return _finish(LabelResult(fixed, attempt + 1, None, history, [], notes + n, STATUS_OK, evid, issues))
            fb = feedback_for(cand, source, errs, rules) or "上一個答案沒有通過檢查，請依規則重寫"
            if "OCR-DOUBT" in errs:
                fb += "；不要使用標題裡無法確認的字：" + "、".join(f"「{x}」" for x in avoid)
    # 規則 FB-4：重寫後仍不合格、或 AI 沒回應 → 詞界式規則備援（不截前 N 字）
    label, bad = fallback_label(source, rules, avoid)
    notes.append(_note(LEVEL_INFO, REASON_FALLBACK))
    status = STATUS_OK
    if bad:
        status = STATUS_PENDING
        notes.append(_note(LEVEL_CHECK, REASON_PENDING, detail="未通過：" + "、".join(bad)))
    notes += [n for n in review_notes(label, source) if n not in notes]
    return _finish(LabelResult(label, len(history), "FB-4", history, [], notes, status))


def _finish(res: LabelResult) -> LabelResult:
    """reasons = every note's reason (all levels, as before); the level of each lives in notes and
    decides the review list / yellow fill."""
    res.reasons = list(dict.fromkeys(n["reason"] for n in res.notes))
    return res
