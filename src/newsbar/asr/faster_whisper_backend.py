"""faster-whisper backend: Apple GPU (MPS) on a Mac, NVIDIA GPU (CUDA) on a server, CPU otherwise.

Install: `uv sync --extra asr` (Mac) or `uv sync --extra gpu` (NVIDIA). Device and precision can be
forced with NEWSBAR_ASR_DEVICE (auto | cuda | mps | cpu) and NEWSBAR_ASR_COMPUTE (float16 | int8 | ...),
which `newsbar run` sets from `asr.device` / `asr.compute_type` in the rules file.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from faster_whisper import WhisperModel  # noqa: F401  (ImportError → backend unavailable)

from . import AsrSegment


def cuda_device_count() -> int:
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count()
    except Exception:
        return 0


def pick_device(device: str = "auto", platform: str | None = None, cuda_count: int | None = None,
                compute_type: str | None = None) -> tuple[str, str]:
    """Return (device, compute_type) for CTranslate2.

    auto: Mac → MPS float16; NVIDIA GPU present → CUDA float16; otherwise CPU int8.
    float16 is what CTranslate2 recommends on CUDA; int8 keeps CPU runs fast.
    """
    platform = platform or sys.platform
    if device == "auto":
        if platform == "darwin":
            device = "mps"
        elif (cuda_count if cuda_count is not None else cuda_device_count()) > 0:
            device = "cuda"
        else:
            device = "cpu"
    default = {"cuda": "float16", "mps": "float16"}.get(device, "int8")
    return device, compute_type or default


class FasterWhisperBackend:
    name = "faster-whisper"

    def __init__(self, model: str | None = None, device: str = "auto", compute_type: str | None = None):
        from faster_whisper import WhisperModel

        # Measured on the 11/06 TVBS audio (M4): small/CPU int8 ≈ 0.6× real time,
        # small/MPS float16 ≈ 0.13× real time. NVIDIA GPU: "large-v3-turbo" on CUDA float16 (not measured yet).
        self.model_name = model
        if device == "auto":
            device = os.environ.get("NEWSBAR_ASR_DEVICE", "auto")
        compute_type = compute_type or os.environ.get("NEWSBAR_ASR_COMPUTE") or None
        dev, ct = pick_device(device, compute_type=compute_type)
        if dev == "mps":
            try:
                self._m = WhisperModel(model or "small", device="mps", compute_type=ct)
                return
            except Exception:  # MPS backend is experimental in CTranslate2
                dev, ct = "cpu", "int8"
        self._m = WhisperModel(model or "small", device=dev, compute_type=ct)
        self.device, self.compute_type = dev, ct

    def transcribe(self, wav: Path) -> list[AsrSegment]:  # pragma: no cover - needs model
        segs, _ = self._m.transcribe(str(wav), language="zh", vad_filter=True,
                                     initial_prompt="以下是台灣新聞報導，使用繁體中文。")
        return [AsrSegment(s.start, s.end, s.text.strip()) for s in segs]
