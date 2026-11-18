"""Apple Vision OCR (macOS dev backend; built-in zh-Hant, no model download)."""

from __future__ import annotations

from pathlib import Path

import Quartz  # noqa: F401  (pyobjc-framework-Quartz, pulled in by Vision)
import Vision
from Foundation import NSURL

from .base import OcrLine


class VisionBackend:
    name = "vision"
    # Vision reports coarse, quantised confidences for zh-Hant (0.3 / 0.5 / 1.0),
    # so its threshold differs from PaddleOCR's continuous score (TXT-4).
    min_conf = 0.3

    def __init__(self, languages: tuple[str, ...] = ("zh-Hant", "en-US"), accurate: bool = True):
        self.languages = list(languages)
        self.level = (
            Vision.VNRequestTextRecognitionLevelAccurate if accurate else Vision.VNRequestTextRecognitionLevelFast
        )

    def read(self, image: Path) -> list[OcrLine]:
        req = Vision.VNRecognizeTextRequest.alloc().init()
        req.setRecognitionLevel_(self.level)
        req.setRecognitionLanguages_(self.languages)
        req.setUsesLanguageCorrection_(True)
        handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(
            NSURL.fileURLWithPath_(str(image)), None
        )
        ok, err = handler.performRequests_error_([req], None)
        if not ok:
            raise RuntimeError(f"Vision OCR 失敗：{err}")
        lines: list[OcrLine] = []
        for obs in req.results() or []:
            cands = obs.topCandidates_(1)
            if not cands:
                continue
            c = cands[0]
            bb = obs.boundingBox()  # normalised, origin bottom-left
            x, y, w, h = bb.origin.x, bb.origin.y, bb.size.width, bb.size.height
            lines.append(OcrLine(str(c.string()), float(c.confidence()), (x, 1 - y - h, w, h)))
        return lines
