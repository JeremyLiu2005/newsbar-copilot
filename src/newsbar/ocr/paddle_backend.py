"""PaddleOCR backend (NVIDIA GPU server, or CPU). Imported lazily.

Pinned to PaddleOCR 2.x (`paddleocr<3`): 3.x changed both the constructor and the result format.
The GPU is used when Paddle was built with CUDA and sees a device; NEWSBAR_OCR_GPU=0/1 forces it.
"""

from __future__ import annotations

import os
from pathlib import Path

from .base import OcrLine


def paddle_has_gpu() -> bool:
    forced = os.environ.get("NEWSBAR_OCR_GPU")
    if forced in ("0", "1"):
        return forced == "1"
    try:
        import paddle

        return bool(paddle.device.is_compiled_with_cuda()) and paddle.device.cuda.device_count() > 0
    except Exception:
        return False


class PaddleBackend:
    name = "paddle"
    min_conf = 0.6

    def __init__(self, lang: str = "chinese_cht", use_gpu: bool | None = None):
        try:
            from paddleocr import PaddleOCR
        except ImportError as e:  # pragma: no cover
            raise ImportError("請先安裝 PaddleOCR：uv sync --extra gpu（NVIDIA）或 uv add 'paddleocr<3' paddlepaddle（CPU）") from e
        self.use_gpu = paddle_has_gpu() if use_gpu is None else use_gpu
        self._ocr = PaddleOCR(lang=lang, use_angle_cls=False, use_gpu=self.use_gpu, show_log=False)

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
