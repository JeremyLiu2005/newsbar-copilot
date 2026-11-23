"""Chinese word boundaries for label building and checking (LBL-9).

jieba's dictionary is Simplified Chinese and splits Traditional titles badly
(違規害/婦撞亡, 前員/工蘿拉涉). On macOS the system ICU word tokenizer (through
CoreFoundation, already installed with the Vision OCR backend) knows Traditional
Chinese: 違規/害/婦/撞/亡, 前/員工. Elsewhere (the NVIDIA/Linux build) jieba is the
fallback, and with neither every position counts as a boundary (no check).
"""

from __future__ import annotations

import threading
from functools import lru_cache


_LOCK = threading.Lock()
_CF: dict | None = None  # bound once: pyobjc resolves CoreFoundation names lazily and that lookup is
# not thread-safe (labels are made in parallel threads: KeyError 'CFStringTokenizerAdvanceToNextToken')


def _cf() -> dict | None:
    global _CF
    if _CF is None:
        try:
            import CoreFoundation as CF  # noqa: PLC0415  (pyobjc, macOS only)

            _CF = {
                "locale": CF.CFLocaleCreate(None, "zh_TW"),
                "create": CF.CFStringTokenizerCreate,
                "advance": CF.CFStringTokenizerAdvanceToNextToken,
                "range": CF.CFStringTokenizerGetCurrentTokenRange,
                "unit": CF.kCFStringTokenizerUnitWordBoundary,
            }
        except Exception:  # noqa: BLE001
            _CF = {}
    return _CF or None


def _icu_tokens(text: str) -> list[tuple[int, int]] | None:
    with _LOCK:
        cf = _cf()
        if cf is None:
            return None
        tk = cf["create"](None, text, (0, len(text)), cf["unit"], cf["locale"])
        out = []
        while cf["advance"](tk):
            r = cf["range"](tk)
            out.append((r.location, r.location + r.length))
        return out


def _jieba_tokens(text: str, hmm: bool = True) -> list[tuple[int, int]] | None:
    try:
        import jieba  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return None
    jieba.setLogLevel(60)
    out, pos = [], 0
    for t in jieba.cut(text, HMM=hmm):
        out.append((pos, pos + len(t)))
        pos += len(t)
    return out


@lru_cache(maxsize=4096)
def tokens(text: str) -> tuple[tuple[int, int], ...]:
    """(start, end) spans of the words in `text`; characters between spans (spaces) are skipped."""
    spans = _icu_tokens(text)
    if spans is None:
        spans = _jieba_tokens(text)
    if spans is None:
        spans = [(i, i + 1) for i in range(len(text))]
    return tuple(spans)


@lru_cache(maxsize=4096)
def _dict_words(text: str) -> tuple[tuple[int, int], ...]:
    """jieba dictionary words only (HMM off: no invented words such as 違規害). The dictionary is
    Simplified, so it only knows words whose characters are the same in both scripts (美股, 元凶,
    買一送一, 提告) — exactly the ones ICU sometimes splits."""
    spans = _jieba_tokens(text, hmm=False) or []
    return tuple((s, e) for s, e in spans if e - s > 1)


def boundaries(text: str) -> set[int]:
    """Positions where a word starts or ends (0 and len(text) included). A position inside an ICU
    word or inside a jieba dictionary word is not a boundary."""
    out = {0, len(text)}
    toks = tokens(text)
    for s, e in toks:
        out.add(s)
        out.add(e)
    for s, e in _dict_words(text):
        out.difference_update(range(s + 1, e))
    # 新聞常見詞，jieba 繁體字典常拆錯（APEC峰|會、稱下|週、台積|電、人聲|押禁見）：整詞內部不算詞界，兩端算詞界
    for w in _NEWS_WORDS:
        i = text.find(w)
        while i >= 0:
            out.difference_update(range(i + 1, i + len(w)))
            out.update((i, i + len(w)))
            i = text.find(w, i + 1)
    # ICU breaks a short word it does not know (美股) into single characters. Exactly two adjacent
    # single content characters between real words are kept together; a longer run means ICU found
    # nothing in that stretch (男二度毒駕拒檢催 …, 遭車撞飛), and gluing it would forbid fair cuts.
    i = 0
    while i < len(toks):
        j = i
        while (j < len(toks) and toks[j][1] - toks[j][0] == 1 and _content(text[toks[j][0]])
               and (j == i or toks[j][0] == toks[j - 1][1])):
            j += 1
        if j - i == 2:
            out.difference_update(range(toks[i][0] + 1, toks[j - 1][1]))
        elif j - i >= 3 and text[toks[i][0]] in _SURNAMES:
            out.difference_update(range(toks[i][0] + 1, toks[i][0] + 3))  # 江啟臣, 蔡阿嘎: surname + 2
        i = max(j, i + 1)
    return out


# Words jieba's Simplified dictionary splits in Traditional news titles; never cut inside them.
_NEWS_WORDS = ("峰會", "上週", "下週", "本週", "上月", "下月", "台積電", "聯發科", "鴻海", "年線", "季線", "月線",
               "立委", "議員", "市長", "縣長", "總統", "院長", "部長", "警方", "消防", "颱風", "地震",
               "聲押", "羈押", "禁見", "交保", "起訴", "判刑")


# Common Taiwanese surnames: a run of single characters that starts with one is most likely a name.
_SURNAMES = set("陳林黃張李王吳劉蔡楊許鄭謝洪郭邱曾廖賴徐周葉蘇莊呂江何蕭羅高潘簡朱鍾游彭詹胡施沈余盧梁趙顏柯翁魏孫戴范方宋鄧杜傅侯曹薛丁卓阮馬董温唐藍石蔣古紀姚連馮歐程湯黃田康姜白汪鄒尤巫鐘黎涂龔嚴韓袁金童陸夏柳凃邵錢伍倪溫于譚駱熊任甘秦顧毛章史官萬俞雷粘饒張闕凌崔尹孔辛武辜陶段龍韋葛池孟褚殷麥賀賈莫文管關向包丘梅華利裴樊房全佘左花")
_FUNCTION = set("的了也未在與和及為是將已還更又就都才並且而或遭被把讓給對從向於再仍")


def _content(ch: str) -> bool:
    return "\u4e00" <= ch <= "\u9fff" and ch not in _FUNCTION


def cuts_word(text: str, start: int, end: int) -> bool:
    """True when text[start:end] begins or ends inside a multi-character word of `text`."""
    b = boundaries(text)
    return start not in b or end not in b


def is_word(text: str) -> bool:
    """True when `text` (2+ characters) is one word on its own (ICU or jieba dictionary)."""
    if len(text) < 2:
        return False
    return tokens(text) == ((0, len(text)),) or _dict_words(text) == ((0, len(text)),)


def strong_word_at(text: str, i: int) -> bool:
    """True when position i sits inside a jieba dictionary word (一度, 元凶): a cut there is certain."""
    return any(s < i < e for s, e in _dict_words(text))
