"""OCR check before the short title (規則 OCR-V1 … OCR-V4).

Look back at the evidence before trusting a suspicious character; never guess:
  1. the same title in the story's other frames (a different reading at the same place),
  2. the story's other titles and the same-segment speech (one-character-different real word),
  3. known misreading patterns (config ocr.confusions) — only a clue, they need support.
A correction is applied only with visual support (another frame or another title of the same
story shows the corrected word); speech alone or a known pattern alone leaves the title as read
and marks it 待回查 with the suggestion. A rare character is never a reason by itself (names).
The original text is never overwritten: every record keeps ocr_raw next to ocr_corrected.
"""

from __future__ import annotations

from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

from . import words as wordseg

# 規則 OCR-V3：已知常見誤辨（只當線索，仍要影格或同則標題佐證才修正）
DEFAULT_CONFUSIONS = (("促鍆", "促銷"), ("牙叉", "貨叉"))
STATUS_CLEAN = "無疑義"
STATUS_FIXED = "已修正"
STATUS_PENDING = "待回查"


def _cjk(ch: str) -> bool:
    return "一" <= ch <= "鿿"


def _frames_of(obs, text: str) -> list[str]:
    return [o.frame or f"sec:{o.sec}" for o in obs if o.bar_text == text][:5]


def _record(raw: str) -> dict:
    return {"ocr_raw": raw, "ocr_corrected": None, "correction_reason": None, "supporting_frame_ids": [],
            "supporting_asr_ids": [], "review_status": STATUS_CLEAN, "suspects": []}


WIN = 4


def _one_char_off(window: str, text: str) -> tuple[int, str] | None:
    """(position, matching piece of text) for a piece of text that differs from window in exactly one
    CJK character at the same place."""
    n = len(window)
    for i in range(len(text) - n + 1):
        piece = text[i:i + n]
        diff = [k for k in range(n) if piece[k] != window[k]]
        if len(diff) == 1 and _cjk(piece[diff[0]]):
            return diff[0], piece
    return None


def _pair(text: str, k: int) -> str:
    """The two characters around position k (the word the differing character belongs to)."""
    return text[k - 1:k + 1] if k > 0 else text[k:k + 2]


def _one_off_words(window: str, text: str) -> list[str]:
    """Real words in `text` that differ from the 2-character `window` in exactly one character."""
    out = []
    for i in range(len(text) - 1):
        w = text[i:i + 2]
        if w != window and sum(a == b for a, b in zip(w, window)) == 1 and all(_cjk(c) for c in w) \
                and wordseg.is_word(w):
            out.append(w)
    return list(dict.fromkeys(out))


def char_titles(texts) -> Counter:
    """In how many distinct titles each character appears (the channel's own OCR as the reference)."""
    c: Counter = Counter()
    for t in set(x for x in texts if x):
        c.update(set(t))
    return c


def _rare_swap(a: str, b: str, freq: Counter) -> bool:
    """OCR-V2 clue: the character read is rare in this broadcast while the alternative is common
    (瀆 in 1 title, 渡 in many). A rare character alone is never enough (names are rare too)."""
    return freq.get(a, 0) <= 3 and freq.get(b, 0) >= 3 * max(1, freq.get(a, 0))


def check_title(raw: str, story_titles: list[str], obs: list, asr_text: str,
                confusions=DEFAULT_CONFUSIONS, freq: Counter | None = None) -> dict:
    rec = _record(raw)
    others = [t for t in story_titles if t != raw]
    fixes: list[tuple[str, str, str, list[str], list[str]]] = []  # (wrong, right, why, frames, asr)
    pending: list[tuple[str, str | None, str]] = []
    # 1. 規則 OCR-V1：同一標題的前後影格同位置讀出不同字
    counts = Counter(o.bar_text for o in obs if o.bar_text)
    freq = freq if freq is not None else char_titles(list(counts) + list(story_titles))
    for text, n in counts.items():
        if text == raw or abs(len(text) - len(raw)) > 1 or SequenceMatcher(None, raw, text).ratio() < 0.85:
            continue
        for op, i1, i2, j1, j2 in SequenceMatcher(None, raw, text, autojunk=False).get_opcodes():
            if op != "replace" or i2 - i1 > 2:
                continue
            lo, hi = max(0, i1 - 1), min(len(raw), i2 + 1)
            wrong, right = raw[lo:hi], text[max(0, j1 - (i1 - lo)):j2 + (hi - i2)]
            if n >= 2 and wordseg.is_word(right[:2] if len(right) >= 2 else right) and not wordseg.is_word(wrong[:2]):
                fixes.append((wrong, right, f"相鄰影格讀成「{right}」（{n} 格）", _frames_of(obs, text), []))
            else:
                pending.append((wrong, right, f"相鄰影格讀法不同：「{wrong}」／「{right}」"))
    # 2. 規則 OCR-V2：同則其他標題或同段語音，同一段 4 字只差一個字、且差的那個字在標題裡不成詞
    #    （俊傑偷瀆／俊傑偷渡）。窗口要 4 字以上，避免長語音裡隨便湊到一個近似詞。
    for i in range(len(raw) - WIN + 1):
        w = raw[i:i + WIN]
        if not all(_cjk(c) for c in w) or any(f[0] in w or w in f[0] for f in fixes):
            continue
        seen = None
        for src in others:
            hit = _one_char_off(w, src)
            if hit and not wordseg.is_word(_pair(w, hit[0])) and wordseg.is_word(_pair(hit[1], hit[0])):
                seen = (src, hit)
                break
        heard = _one_char_off(w, asr_text or "")
        heard_pair = _pair(heard[1], heard[0]) if heard else None
        if seen:
            src, (k, alt) = seen
            pair_raw, pair_alt = _pair(w, k), _pair(alt, k)
            # titles of one story are paraphrases and there is no glyph comparison here: another title
            # alone never corrects (惡火／大火 is a rewording). With the rarity clue (瀆 is the odd one out
            # in this broadcast) it becomes a 待回查 suspect the short title must avoid; otherwise a hint.
            if _rare_swap(w[k], alt[k], freq):
                said = "，語音也作此詞" if pair_alt in (asr_text or "") else ""
                pending.append((pair_raw, pair_alt, f"同則標題作「{pair_alt}」{said}；「{w[k]}」在本節標題中罕見，"
                                                    "疑似誤辨，未修正（待重讀截圖）"))
            else:
                rec.setdefault("title_hints", []).append({"text": pair_raw, "other_title": pair_alt})
        elif heard and heard_pair and not wordseg.is_word(_pair(w, heard[0])) and wordseg.is_word(heard_pair):
            rec.setdefault("asr_hints", []).append({"text": _pair(w, heard[0]), "asr": heard_pair})
    # 3. 規則 OCR-V3：已知誤辨模式，有佐證才修正
    for wrong, right in confusions:
        if wrong not in raw or any(wrong in f[0] or f[0] in wrong for f in fixes):
            continue
        frame_hit = [t for t in counts if t != raw and right in t and SequenceMatcher(None, raw, t).ratio() >= 0.7]
        title_hit = [t for t in others if right in t]
        if frame_hit or title_hit:
            src = (frame_hit or title_hit)[0]
            fixes.append((wrong, right, f"已知誤辨「{wrong}→{right}」，且「{src}」可佐證", _frames_of(obs, src), []))
        else:
            asr = [right] if right in (asr_text or "") else []
            pending.append((wrong, right, f"疑似已知誤辨「{wrong}→{right}」，" +
                            ("語音有此詞但" if asr else "") + "同段影格與標題都沒有佐證，未修正"))
    if fixes:
        out = raw
        for wrong, right, why, frames, asr in fixes:
            out = out.replace(wrong, right)
            rec["supporting_frame_ids"] += frames
            rec["supporting_asr_ids"] += asr
        rec["ocr_corrected"] = out
        rec["correction_reason"] = "；".join(f[2] for f in fixes)
        rec["review_status"] = STATUS_FIXED
    if pending:
        rec["suspects"] = [{"text": w, "suggestion": r, "why": why, "pattern": why.startswith("疑似已知誤辨")}
                           for w, r, why in pending]
        rec["review_status"] = STATUS_PENDING
        rec["correction_reason"] = "；".join(filter(None, [rec["correction_reason"]] + [p[2] for p in pending]))
    return rec


def check_story(bar_texts: list[str], obs: list, asr_text: str, confusions=DEFAULT_CONFUSIONS,
                freq: Counter | None = None) -> tuple[list[str], list[dict], set[str]]:
    """→ (titles to build the short title from, one record per title, doubtful pieces to avoid)."""
    recs = [check_title(t, bar_texts, obs, asr_text, confusions, freq) for t in bar_texts]
    titles = [r["ocr_corrected"] or r["ocr_raw"] for r in recs]
    # a known-pattern suspect: none of its characters may be copied (旅展促｜午餐 would keep half of 促鍆)
    avoid = {s["text"] for r in recs for s in r["suspects"]}
    avoid |= {ch for r in recs for s in r["suspects"] if s.get("pattern") for ch in s["text"]}
    return titles, recs, avoid


def confusions_from(rules: dict | None):
    got = ((rules or {}).get("ocr") or {}).get("confusions")
    return tuple(tuple(x) for x in got) if got else DEFAULT_CONFUSIONS


def reread_frame(path: Path) -> dict:
    """OCR-V4: re-read one saved title frame with the local OCR backend. Without a backend the
    result says so — the title is then NOT re-verified and nobody may claim it was."""
    path = Path(path)
    if not path.exists():
        return {"status": "not_reverified", "why": "影格不存在", "text": None}
    try:
        from .ocr.base import get_backend, join_lines
        text, conf = join_lines(get_backend("vision").read(path))
    except Exception as e:  # noqa: BLE001
        return {"status": "not_reverified", "why": f"無法重讀：{type(e).__name__}", "text": None}
    return {"status": "reread", "text": text, "conf": conf, "frame": str(path)}
