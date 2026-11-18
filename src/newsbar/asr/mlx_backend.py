"""mlx-whisper (Apple Silicon GPU) — dev backend. `uv add mlx-whisper` to enable."""

from __future__ import annotations

from pathlib import Path

import mlx_whisper  # noqa: F401  (ImportError → backend unavailable)

from . import AsrSegment

DEFAULT_MODEL = "mlx-community/whisper-large-v3-turbo"


class MlxWhisperBackend:
    name = "mlx"

    def __init__(self, model: str | None = None):
        self.model = model or DEFAULT_MODEL

    def transcribe(self, wav: Path) -> list[AsrSegment]:  # pragma: no cover - needs model
        import mlx_whisper

        res = mlx_whisper.transcribe(str(wav), path_or_hf_repo=self.model, language="zh",
                                     initial_prompt="以下是台灣新聞報導，使用繁體中文。")
        return [AsrSegment(float(s["start"]), float(s["end"]), s["text"].strip()) for s in res["segments"]]
