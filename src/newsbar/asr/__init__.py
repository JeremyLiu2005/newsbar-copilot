"""Speech → per-second asr_text / topic / ad_speech (architecture §4.3).

ASR is an auxiliary signal only (never a label by itself unless OCR fails,
FB-3). Topic ids come from a lexical TextTiling-style segmentation over the
transcript; replace with sentence embeddings in production.
"""

from __future__ import annotations

import math
import re
import subprocess
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..ingest import _ffbin
from ..schemas import SecondObs

AD_WORDS = ("特價", "優惠", "限時", "折扣", "打折", "專線", "熱線", "訂購", "立即", "官網", "現在打", "下單",
            "免運", "分期", "零利率", "促銷", "買一送一", "加贈", "售價", "只要")


@dataclass
class AsrSegment:
    start: float
    end: float
    text: str


class AsrBackend(Protocol):
    name: str

    def transcribe(self, wav: Path) -> list[AsrSegment]: ...


def get_backend(name: str = "auto", model: str | None = None) -> AsrBackend:
    if name in ("auto", "mlx"):
        try:
            from .mlx_backend import MlxWhisperBackend
            return MlxWhisperBackend(model)
        except ImportError:
            if name == "mlx":
                raise
    if name in ("auto", "faster-whisper"):
        from .faster_whisper_backend import FasterWhisperBackend
        return FasterWhisperBackend(model)
    raise ValueError(f"unknown ASR backend {name!r}")


def extract_audio(video: Path, out: Path, start: float = 0.0, duration: float | None = None) -> Path:
    cmd = [_ffbin("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y"]
    if start:
        cmd += ["-ss", str(start)]
    cmd += ["-i", str(video)]
    if duration:
        cmd += ["-t", str(duration)]
    cmd += ["-vn", "-ac", "1", "-ar", "16000", str(out)]
    subprocess.run(cmd, check=True)
    return out


def is_ad_speech(text: str) -> bool:
    return sum(1 for w in AD_WORDS if w in text) >= 1 and bool(re.search(r"\d|元|塊", text)) or \
        sum(1 for w in AD_WORDS if w in text) >= 2


def _bigrams(text: str) -> Counter:
    t = re.sub(r"[^一-鿿A-Za-z0-9]", "", text)
    return Counter(t[i : i + 2] for i in range(len(t) - 1))


def _cos(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    dot = sum(v * b.get(k, 0) for k, v in a.items())
    return dot / math.sqrt(sum(v * v for v in a.values()) * sum(v * v for v in b.values()))


def assign_topics(segs: list[AsrSegment], window: int = 3, threshold: float = 0.06) -> list[str]:
    """TextTiling-lite: score each gap by the bigram cosine between the
    `window` segments before and after it; a boundary is a local minimum of
    that score that is also below `threshold`."""
    n = len(segs)
    gaps = [1.0] * n  # gaps[i] = similarity across the gap before segment i
    for i in range(1, n):
        before = sum((_bigrams(s.text) for s in segs[max(0, i - window) : i]), Counter())
        after = sum((_bigrams(s.text) for s in segs[i : i + window]), Counter())
        gaps[i] = _cos(before, after)
    topics: list[str] = []
    tid = 0
    for i in range(n):
        if i > 0:
            left = gaps[i - 1] if i - 1 >= 1 else 1.0
            right = gaps[i + 1] if i + 1 < n else 1.0
            if gaps[i] < threshold and gaps[i] <= left and gaps[i] <= right:
                tid += 1
        topics.append(f"t{tid}")
    return topics


def segment_pitch(samples, sr: int, seg: AsrSegment) -> float | None:
    """Median fundamental frequency (Hz) of voiced 40 ms frames via autocorrelation."""
    import numpy as np

    a, b = int(seg.start * sr), int(seg.end * sr)
    x = np.asarray(samples[a:b], dtype=np.float32)
    n = int(0.04 * sr)
    lo, hi = int(sr / 400), int(sr / 70)  # 70–400 Hz
    f0s = []
    for i in range(0, len(x) - n, n):
        fr = x[i : i + n] - x[i : i + n].mean()
        if fr.std() < 0.01:
            continue
        ac = np.correlate(fr, fr, "full")[n - 1 :]
        lag = lo + int(np.argmax(ac[lo:hi]))
        if ac[lag] > 0.3 * ac[0]:
            f0s.append(sr / lag)
    return float(np.median(f0s)) if len(f0s) >= 3 else None


def assign_speakers(pitches: list[float | None], ratio: float = 1.25) -> list[str | None]:
    """NEW-2c: a new speaker id whenever the pitch jumps by more than `ratio`."""
    out: list[str | None] = []
    sid, last = 0, None
    for p in pitches:
        if p is None:
            out.append(None)
            continue
        if last is not None and max(p, last) / min(p, last) > ratio:
            sid += 1
        last = p
        out.append(f"spk{sid}")
    return out


def attach(
    obs: list[SecondObs],
    backend: AsrBackend,
    video: Path | None = None,
    sec_offset: int = 0,
    start: float = 0.0,
    duration: float | None = None,
    segments: list[AsrSegment] | None = None,
) -> list[AsrSegment]:
    """Fill asr_text / topic / ad_speech on obs (in place). Pass `segments` to skip ASR."""
    speakers: list[str | None] = []
    if segments is None:
        import wave

        import numpy as np

        with tempfile.TemporaryDirectory() as d:
            wav = extract_audio(video, Path(d) / "a.wav", start, duration)  # type: ignore[arg-type]
            segments = backend.transcribe(wav)
            with wave.open(str(wav)) as w:
                sr = w.getframerate()
                pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
            speakers = assign_speakers([segment_pitch(pcm, sr, s) for s in segments])
    topics = assign_topics(segments)
    speakers = speakers or [None] * len(segments)
    by_sec = {o.sec: o for o in obs}
    for seg, topic, spk in zip(segments, topics, speakers):
        ad = is_ad_speech(seg.text)
        for t in range(int(seg.start), int(math.ceil(seg.end)) or int(seg.start) + 1):
            o = by_sec.get(t + sec_offset)
            if o is None:
                continue
            o.asr_text = seg.text
            o.topic = topic
            o.speaker = spk
            o.ad_speech = o.ad_speech or ad
    return segments
