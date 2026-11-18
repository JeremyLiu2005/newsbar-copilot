"""OCR backend interface (NFR-05): backends differ, output does not."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass
class OcrLine:
    text: str
    conf: float
    box: tuple[float, float, float, float]  # x, y, w, h in 0–1, origin top-left


class OcrBackend(Protocol):
    name: str

    def read(self, image: Path) -> list[OcrLine]: ...


def join_lines(lines: list[OcrLine]) -> tuple[str | None, float]:
    """Reading order: top-to-bottom rows, left-to-right within a row."""
    if not lines:
        return None, 0.0
    rows: list[list[OcrLine]] = []
    for ln in sorted(lines, key=lambda l: (l.box[1], l.box[0])):
        if rows and abs(rows[-1][0].box[1] - ln.box[1]) < max(ln.box[3], rows[-1][0].box[3]) * 0.5:
            rows[-1].append(ln)
        else:
            rows.append([ln])
    parts = [" ".join(l.text for l in sorted(r, key=lambda l: l.box[0])) for r in rows]
    text = " ".join(p for p in parts if p).strip()
    weights = [len(l.text) for l in lines]
    conf = sum(l.conf * w for l, w in zip(lines, weights)) / max(1, sum(weights))
    return (text or None), conf


def get_backend(name: str = "auto") -> OcrBackend:
    if name in ("auto", "vision"):
        try:
            from .vision_backend import VisionBackend
            return VisionBackend()
        except ImportError:
            if name == "vision":
                raise
    if name in ("auto", "paddle"):
        from .paddle_backend import PaddleBackend
        return PaddleBackend()
    raise ValueError(f"unknown OCR backend {name!r}")
