"""Time guarded ASR on one full channel: python tools/asr_guard_check.py data/raw/tvbs.ts base"""
import sys
import tempfile
import time
from pathlib import Path

from newsbar.asr import extract_audio, transcribe_guarded


class B:
    name = "faster-whisper"

    def __init__(self, model):
        self.model_name = model


if __name__ == "__main__":
    d = Path(tempfile.mkdtemp())
    w = extract_audio(Path(sys.argv[1]), d / "a.wav")
    t = time.time()
    segs = transcribe_guarded(B(sys.argv[2]), w, 3600)
    print("result", None if segs is None else len(segs), "segs in", round(time.time() - t, 1), "s")
