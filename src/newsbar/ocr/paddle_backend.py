"""PaddleOCR backend (production GPU server). Imported lazily."""

from __future__ import annotations

from pathlib import Path

from .base import OcrLine


class PaddleBackend:
    name = "paddle"
    min_conf = 0.6

    def __init__(self, lang: str = "chinese_cht", use_gpu: bool = True):
        try:
            from paddleocr import PaddleOCR
        except ImportError as e:  # pragma: no cover
            raise ImportError("請先安裝 paddleocr：uv add paddleocr paddlepaddle") from e
        self._ocr = PaddleOCR(lang=lang, use_angle_cls=False, use_gpu=use_gpu, show_log=False)

    def read(self, image: Path) -> list[OcrLine]:  # pragma: no cover - needs paddle
        from PIL import Image

        w, h = Image.open(image).size
        res = self._ocr.ocr(str(image), cls=False) or [[]]
        lines = []
        for pts, (text, conf) in res[0] or []:
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            lines.append(OcrLine(text, float(conf), (min(xs) / w, min(ys) / h, (max(xs) - min(xs)) / w, (max(ys) - min(ys)) / h)))
        return lines
