"""faster-whisper (CUDA/CPU) — production backend. `uv add faster-whisper` to enable."""

from __future__ import annotations

from pathlib import Path

from faster_whisper import WhisperModel  # noqa: F401  (ImportError → backend unavailable)

from . import AsrSegment


class FasterWhisperBackend:
    name = "faster-whisper"

    def __init__(self, model: str | None = None, device: str = "auto"):
        from faster_whisper import WhisperModel

        self._m = WhisperModel(model or "large-v3-turbo", device=device, compute_type="auto")

    def transcribe(self, wav: Path) -> list[AsrSegment]:  # pragma: no cover - needs model
        segs, _ = self._m.transcribe(str(wav), language="zh", vad_filter=True,
                                     initial_prompt="以下是台灣新聞報導，使用繁體中文。")
        return [AsrSegment(s.start, s.end, s.text.strip()) for s in segs]
