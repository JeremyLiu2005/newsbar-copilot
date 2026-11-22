"""Word lists the labeler, segmenter and text filter share (place names, weather words,
social-news keywords, hedge markers, on-screen overlay tokens).

The defaults ship inside the package (lexicon_default.yaml). A deployment overrides any key by
listing it in a yaml file (config/lexicon.yaml); a listed key replaces the default for that key.
Nothing here is tuned per recording: editing the yaml needs no code change.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_PATH = Path(__file__).with_name("lexicon_default.yaml")
_STRIP_SUFFIX = "區鄉鎮市"


@dataclass(frozen=True)
class Lexicon:
    social_keywords: tuple[str, ...]
    weather_words: tuple[str, ...]
    hedge_markers: tuple[str, ...]
    hedge_exclusions: tuple[str, ...]
    overlay_tokens: tuple[str, ...]
    overlay_patterns: tuple[str, ...]
    canon_of: dict[str, str]  # every surface form → canonical short name (土城區 → 土城)
    variants: dict[str, tuple[str, ...]]  # canonical → every surface form
    _place_re: re.Pattern = field(repr=False, default=None)  # type: ignore[assignment]

    # ---- places -------------------------------------------------------------------------
    def find_places(self, text: str) -> list[tuple[int, str, str]]:
        """(position, surface form, canonical name), longest match first so that 新北市 is not
        also read as 北市, in text order."""
        if not self._place_re:
            return []
        return [(m.start(), m.group(), self.canon_of[m.group()]) for m in self._place_re.finditer(text)]

    def places_in(self, text: str) -> list[str]:
        out: list[str] = []
        for _, _, canon in self.find_places(text):
            if canon not in out:
                out.append(canon)
        return out

    def place_in_label(self, canon: str, label: str) -> bool:
        return any(v in label for v in self.variants.get(canon, (canon,)))

    # ---- hedges -------------------------------------------------------------------------
    def hedge_hits(self, text: str) -> list[tuple[int, str]]:
        """(position, marker) of every hedge marker, ignoring words that merely contain the
        character (宣傳, 恐怖, 懷疑 …). Positions refer to the original text."""
        masked = text
        for ex in sorted(self.hedge_exclusions, key=len, reverse=True):
            masked = masked.replace(ex, "\0" * len(ex))
        hits = []
        i = 0
        markers = sorted(self.hedge_markers, key=len, reverse=True)
        while i < len(masked):
            for m in markers:
                if masked.startswith(m, i):
                    hits.append((i, m))
                    i += len(m) - 1
                    break
            i += 1
        return hits

    def has_hedge(self, text: str) -> bool:
        return bool(self.hedge_hits(text))

    def has_strong_hedge(self, text: str) -> bool:
        """A hedge of two or more characters (疑似, 涉嫌, 據傳 …); a lone 疑/傳/恐/涉 is too ambiguous
        to reject a label over, it is only flagged for review."""
        return any(len(m) >= 2 for _, m in self.hedge_hits(text))

    def has_strong_hedge(self, text: str) -> bool:
        """A hedge of two or more characters (疑似, 涉嫌, 據傳 …); a lone 疑/傳/恐/涉 is too ambiguous
        to reject a label over, it is only flagged for review."""
        return any(len(m) >= 2 for _, m in self.hedge_hits(text))

    # ---- overlay tokens -----------------------------------------------------------------
    def strip_overlay(self, text: str | None) -> str | None:
        """Drop standalone overlay tokens from one main-bar reading; None if nothing is left."""
        if not text:
            return text
        out = text
        for tok in sorted(self.overlay_tokens, key=len, reverse=True):
            if re.fullmatch(r"[A-Za-z0-9 ]+", tok):  # ASCII tag: also when glued to Chinese text
                out = re.sub(rf"(?<![A-Za-z]){re.escape(tok)}(?![A-Za-z])", " ", out, flags=re.I)
            if re.match(r"[A-Za-z]", tok) and not re.fullmatch(r"[A-Za-z0-9 ]+", tok):
                # LIVE直播 style: ASCII head glued to a Chinese tail
                out = re.sub(rf"(?<![A-Za-z]){re.escape(tok)}", " ", out, flags=re.I)
        segs = [s for s in re.split(r"\s+", out) if s]
        toks = {t.lower() for t in self.overlay_tokens}
        pats = [re.compile(p) for p in self.overlay_patterns]
        keep = [s for s in segs if s.lower() not in toks and not any(p.match(s) for p in pats)]
        return " ".join(keep) or None


def _build(raw: dict) -> Lexicon:
    stop = set(raw["place_stopwords"])
    canon_of: dict[str, str] = {}
    extra: dict[str, list[str]] = {}

    def add(surface: str, canon: str) -> None:
        for s in {surface, surface.replace("台", "臺")}:
            canon_of.setdefault(s, canon)

    for c in raw["counties"]:
        add(c, c)
        add(c + "市", c)
        add(c + "縣", c)
    for short, full in raw["short_forms"].items():
        add(short, full)
    for names in raw["districts"].values():
        for n in names:
            short = n[:-1] if n[-1] in _STRIP_SUFFIX and len(n) > 2 else n
            if short in stop:  # 成功鎮 is a place, bare 成功 is not (成功逮捕)
                if n != short:
                    add(n, short)
                    extra.setdefault(short, []).append(short)  # a label may still say 成功
                continue
            add(n, short)
            add(short, short)
    for lm in raw["landmarks"]:
        add(lm, lm)
    variants: dict[str, list[str]] = {}
    for s, c in canon_of.items():
        variants.setdefault(c, []).append(s)
    for c, forms in extra.items():
        variants.setdefault(c, []).extend(f for f in forms if f not in variants[c])
    names = sorted(canon_of, key=len, reverse=True)
    rx = re.compile("|".join(re.escape(n) for n in names)) if names else None
    return Lexicon(
        social_keywords=tuple(raw["social_keywords"]),
        weather_words=tuple(raw["weather_words"]),
        hedge_markers=tuple(raw["hedge_markers"]),
        hedge_exclusions=tuple(raw["hedge_exclusions"]),
        overlay_tokens=tuple(raw["overlay_tokens"]),
        overlay_patterns=tuple(raw["overlay_patterns"]),
        canon_of=canon_of,
        variants={k: tuple(v) for k, v in variants.items()},
        _place_re=rx,
    )


def load(path: str | Path | None = None) -> Lexicon:
    raw = yaml.safe_load(DEFAULT_PATH.read_text(encoding="utf-8"))
    if path:
        over = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        unknown = set(over) - set(raw)
        if unknown:
            raise ValueError(f"詞庫有不認得的鍵：{', '.join(sorted(unknown))}（可用：{', '.join(raw)}）")
        raw.update(over)
    return _build(raw)


_active: Lexicon | None = None


def get() -> Lexicon:
    global _active
    if _active is None:
        _active = load()
    return _active


def use(path: str | Path | None) -> Lexicon:
    """Make `path` (or the built-in defaults) the lexicon every module reads."""
    global _active
    _active = load(path)
    return _active
