"""On-screen clock → video start time (spec MAP-2; FR-24).

Every channel shows HH:MM in a corner. Reading it each second for the first
couple of minutes, the second where the minute flips is HH:MM:00, so
start_clock = flip_minute − flip_second. Accuracy ±1 s (one frame per second).
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from . import ingest
from .ocr.base import OcrBackend, join_lines

_HHMM = re.compile(r"([01]?\d|2[0-3])\s*[:：.]\s*([0-5]\d)")


def parse_hhmm(text: str | None) -> int | None:
    if not text:
        return None
    m = _HHMM.search(text)
    return int(m[1]) * 60 + int(m[2]) if m else None


def find_flip(readings: list[int | None]) -> tuple[int, int] | None:
    """Return (second index, minute-of-day) of the first stable minute increase.

    A flip must be confirmed by the next two readings to ignore OCR noise.
    """
    clean = [(i, v) for i, v in enumerate(readings) if v is not None]
    for k in range(1, len(clean) - 2):
        (i0, a), (i1, b) = clean[k - 1], clean[k]
        if b == a + 1 and clean[k + 1][1] == b and clean[k + 2][1] == b:
            return i1, b
    return None


def start_clock(video: Path, clock_roi: list[float], backend: OcrBackend, probe_sec: int = 130) -> str | None:
    """HH:MM:SS of the video's first frame, or None if no flip was seen."""
    with tempfile.TemporaryDirectory() as d:
        dirs = ingest.extract_rois(video, {"main_bar": clock_roi}, Path(d), duration=probe_sec)
        imgs = sorted(dirs["main_bar"].glob("*.jpg"))
        readings = [parse_hhmm(join_lines(backend.read(p))[0]) for p in imgs]
    flip = find_flip(readings)
    if flip is None:
        return None
    sec, minute = flip
    t = minute * 60 - sec
    t %= 24 * 3600
    return f"{t // 3600:02d}:{t % 3600 // 60:02d}:{t % 60:02d}"
