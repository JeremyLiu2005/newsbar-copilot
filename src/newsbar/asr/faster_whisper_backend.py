"""faster-whisper (CUDA/CPU) — production backend. `uv add faster-whisper` to enable."""

from __future__ import annotations

from pathlib import Path

from faster_whisper import WhisperModel  # noqa: F401  (ImportError → backend unavailable)

from . import AsrSegment


class FasterWhisperBackend:
    name = "faster-whisper"

    def __init__(self, model: str | None = None, device: str = "auto"):
        from faster_whisper import WhisperModel

        # Measured on the 11/06 TVBS audio (M4): small/CPU int8 ≈ 0.6× real time,
        # small/MPS float16 ≈ 0.13× real time. GPU server: "large-v3-turbo" on CUDA.
        import sys

        self.model_name = model

        if device == "auto" and sys.platform == "darwin":
            try:
                self._m = WhisperModel(model or "small", device="mps", compute_type="float16")
                return
            except Exception:  # MPS backend is experimental in CTranslate2
                device = "cpu"
        self._m = WhisperModel(model or "small", device=device, compute_type="int8" if device in ("cpu", "auto") else "auto")

    def transcribe(self, wav: Path) -> list[AsrSegment]:  # pragma: no cover - needs model
        segs, _ = self._m.transcribe(str(wav), language="zh", vad_filter=True,
                                     initial_prompt="以下是台灣新聞報導，使用繁體中文。")
        return [AsrSegment(s.start, s.end, s.text.strip()) for s in segs]
