"""Per-second OCR reads → voted bar text stream (spec TXT-1, TXT-3, TXT-4).

Steps for one channel:
  1. read every main_bar crop (one per second)
  2. drop reads with confidence < min_conf
  3. vote in a sliding window, clustering near-identical reads (OCR noise)
  4. hysteresis: a reading ≥snap_ratio similar to the previous second keeps the
     previous string, so 1-character noise never looks like a new title
  5. flag horizontally scrolling text (ROI covers the ticker by mistake)
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from pathlib import Path

from ..schemas import SecondObs
from .base import OcrBackend, join_lines

_WS = re.compile(r"\s+")
_QUOTES = str.maketrans("", "", "\"'“”‘’「」『』")  # OCR flips quote styles between frames


def normalize(text: str | None) -> str | None:
    if not text:
        return None
    t = unicodedata.normalize("NFKC", text)
    t = _WS.sub("", t).translate(_QUOTES)
    return t or None


def similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


@dataclass
class RawRead:
    sec: int
    text: str | None
    conf: float


@dataclass(frozen=True)
class VoteParams:
    min_conf: float | None = None  # None → backend.min_conf (default 0.6)
    window_sec: int = 5
    cluster_ratio: float = 0.85
    snap_ratio: float = 0.85


def vote(reads: list[RawRead], p: VoteParams = VoteParams()) -> list[RawRead]:
    reads = sorted(reads, key=lambda r: r.sec)
    min_conf = 0.6 if p.min_conf is None else p.min_conf
    clean = [RawRead(r.sec, normalize(r.text) if r.conf >= min_conf else None, r.conf) for r in reads]
    half = p.window_sec // 2
    out: list[RawRead] = []
    prev: str | None = None
    for i, r in enumerate(clean):
        window = [w for w in clean[max(0, i - half) : i + half + 1] if w.text]
        if not window or (r.text is None and len(window) <= half):
            out.append(RawRead(r.sec, None, 0.0))
            prev = None
            continue
        clusters: list[list[RawRead]] = []
        for w in window:
            for c in clusters:
                if similar(c[0].text, w.text) >= p.cluster_ratio:  # type: ignore[arg-type]
                    c.append(w)
                    break
            else:
                clusters.append([w])
        # Prefer the cluster containing the current read on ties.
        best = max(clusters, key=lambda c: (len(c), any(w.sec == r.sec for w in c)))
        text = Counter(w.text for w in best).most_common(1)[0][0]
        conf = sum(w.conf for w in best) / len(best)
        if prev is not None and text != prev and similar(prev, text) >= p.snap_ratio:
            text = prev
        out.append(RawRead(r.sec, text, conf))
        prev = text
    return out


def scrolling_spans(reads: list[RawRead], window: int = 10, min_moves: int = 6) -> list[tuple[int, int]]:
    """Spans where the text keeps sliding sideways (TXT-3)."""
    moves: list[int] = []
    for a, b in zip(reads, reads[1:]):
        if not a.text or not b.text or a.text == b.text:
            continue
        m = SequenceMatcher(None, a.text, b.text).find_longest_match(0, len(a.text), 0, len(b.text))
        if m.size >= 4 and m.a != m.b:
            moves.append(b.sec)
    spans: list[tuple[int, int]] = []
    i = 0
    while i < len(moves):
        j = i
        while j + 1 < len(moves) and moves[j + 1] - moves[i] <= window:
            j += 1
        if j - i + 1 >= min_moves:
            spans.append((moves[i], moves[j]))
            i = j + 1
        else:
            i += 1
    return spans


def read_dir(backend: OcrBackend, frames: Path, sec_offset: int = 0, every: int = 1,
             thumbs: Path | None = None, same_diff: float = 3.0,
             only: set[int] | None = None) -> tuple[list[RawRead], int]:
    """OCR one crop per second. Skips work two ways (NFR-05):
    * `every` > 1 reads only every n-th second and forward-fills (ticker presence);
    * `thumbs` (tiny grayscale copies) lets an unchanged crop reuse the previous read —
      titles stay 7–45 s, so most seconds need no OCR at all.
    Returns (reads, number of OCR calls actually made)."""
    import numpy as np

    from ..ingest import THUMB_H, THUMB_W

    imgs = sorted(frames.glob("*.jpg"))
    th = None
    if thumbs is not None and thumbs.exists():
        raw = np.fromfile(thumbs, dtype=np.uint8)
        n = raw.size // (THUMB_W * THUMB_H)
        th = raw[: n * THUMB_W * THUMB_H].reshape(n, THUMB_H, THUMB_W).astype(np.int16)
    reads: list[RawRead] = []
    calls = 0
    last: tuple[str | None, float] | None = None
    last_thumb = None
    for k, img in enumerate(imgs):
        idx = int(img.stem)
        if only is not None and idx + sec_offset not in only:
            continue
        reuse = False
        if last is not None and every > 1 and k % every:
            reuse = True
        elif last is not None and th is not None and idx < len(th) and last_thumb is not None:
            reuse = float(np.abs(th[idx] - last_thumb).mean()) < same_diff
        if reuse:
            text, conf = last  # type: ignore[misc]
        else:
            text, conf = join_lines(backend.read(img))
            calls += 1
            last = (text, conf)
            if th is not None and idx < len(th):
                last_thumb = th[idx]
        reads.append(RawRead(idx + sec_offset, text, conf))
    return reads, calls


@dataclass
class OcrChannelResult:
    obs: list[SecondObs]
    raw: list[RawRead]
    voted: list[RawRead]
    warnings: list[str]


def ocr_channel(
    backend: OcrBackend,
    roi_dirs: dict[str, Path],
    sec_offset: int = 0,
    params: VoteParams = VoteParams(),
) -> OcrChannelResult:
    raw, calls = read_dir(backend, roi_dirs["main_bar"], sec_offset,
                          thumbs=roi_dirs["main_bar"].parent / "main_bar.thumbs")
    if params.min_conf is None:
        params = VoteParams(getattr(backend, "min_conf", 0.6), params.window_sec, params.cluster_ratio, params.snap_ratio)
    voted = vote(raw, params)
    warnings = []
    # Talk-show style segments (11/06 CTS 說新聞) have no lower bar; the title is the big
    # graphic at the top. Read alt_bar only for the seconds where the main bar is empty.
    if "alt_bar" in roi_dirs:
        blank = {r.sec for r in voted if r.text is None}
        if blank:
            a_reads, a_calls = read_dir(backend, roi_dirs["alt_bar"], sec_offset, only=blank)
            calls += a_calls
            a_voted = {r.sec: r for r in vote(a_reads, params)}
            for r in voted:
                if r.text is None and r.sec in a_voted and a_voted[r.sec].text:
                    r.text, r.conf = a_voted[r.sec].text, a_voted[r.sec].conf
    spans = scrolling_spans(voted)
    for a, b in spans:
        warnings.append(f"TXT-3: main_bar 在 {a}–{b} 秒文字橫向移動，ROI 可能框到跑馬燈")
        for r in voted:
            if a <= r.sec <= b:
                r.text, r.conf = None, 0.0
    ticker: dict[int, str | None] = {}
    if "ticker" in roi_dirs:
        t_reads, t_calls = read_dir(backend, roi_dirs["ticker"], sec_offset, every=5)
        calls += t_calls
        ticker = {r.sec: normalize(r.text) for r in t_reads}
    frames = {int(p.stem) + sec_offset: str(p) for p in roi_dirs["main_bar"].glob("*.jpg")}
    obs = [
        SecondObs(sec=r.sec, bar_text=r.text, bar_conf=r.conf, ticker_text=ticker.get(r.sec), frame=frames.get(r.sec))
        for r in voted
    ]
    blank = sum(1 for r in voted if r.text is None) / max(1, len(voted))
    if blank > 0.2:
        warnings.append(f"OCR 空白率 {blank:.0%}（>20%），請用 calibrate 檢查 ROI")
    res = OcrChannelResult(obs, raw, voted, warnings)
    res.ocr_calls = calls  # type: ignore[attr-defined]
    return res


def save_jsonl(reads: list[RawRead], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in reads:
            f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
