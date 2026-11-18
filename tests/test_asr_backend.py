"""Real ASR on macOS TTS speech. Opt-in: NEWSBAR_ASR_TEST=1 (downloads the 'small' model once)."""

import os
import shutil
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("NEWSBAR_ASR_TEST") != "1" or sys.platform != "darwin" or not shutil.which("say"),
    reason="set NEWSBAR_ASR_TEST=1 on macOS to run",
)


def test_faster_whisper_transcribes_and_topics(tmp_path):
    pytest.importorskip("faster_whisper")
    from newsbar.asr import assign_topics
    from newsbar.asr.faster_whisper_backend import FasterWhisperBackend

    wav = tmp_path / "s.wav"
    subprocess.run(["say", "-v", "Meijia", "-o", str(tmp_path / "s.aiff"),
                    "新北土城一名七旬老翁今天騎車轉彎時遭貨車輾過，送醫後不治。"], check=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(tmp_path / "s.aiff"), "-ar", "16000", "-ac", "1", str(wav)], check=True)
    segs = FasterWhisperBackend("small", device="cpu").transcribe(wav)
    text = "".join(s.text for s in segs)
    assert "土城" in text and "貨車" in text, text
    assert assign_topics(segs)
