"""Ad audio fingerprints (architecture §4.5; spec AD-f; FR-22).

Haitsma–Kalker style: 33 log-spaced bands between 300 Hz and 2 kHz; each
frame gives 32 bits = sign of the band-energy difference across frequency
and time. A second of audio ≈ 31 frames. A second "hits" the library when
some stored second matches with bit-error rate below `max_ber`.

The library grows by itself: every confirmed ad break (≥2 other signals) is
added after a run, so the same commercial on another channel or another day
is recognised by sound alone.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .ingest import _ffbin

SR = 5000
FRAME = 2048 // 4  # 0.1 s
HOP = FRAME // 3
N_BANDS = 33
FRAMES_PER_SEC = SR // HOP  # ≈ 29


def audio_samples(video: Path, start: float = 0.0, duration: float | None = None) -> np.ndarray:
    cmd = [_ffbin("ffmpeg"), "-hide_banner", "-loglevel", "error"]
    if start:
        cmd += ["-ss", str(start)]
    cmd += ["-i", str(video)]
    if duration:
        cmd += ["-t", str(duration)]
    cmd += ["-vn", "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def _band_edges() -> np.ndarray:
    freqs = np.geomspace(300, 2000, N_BANDS + 1)
    return np.clip((freqs / (SR / 2) * (FRAME // 2)).astype(int), 1, FRAME // 2)


def frame_bits(samples: np.ndarray) -> np.ndarray:
    """(n_frames,) uint32 sub-fingerprints."""
    if len(samples) < FRAME:
        return np.zeros(0, dtype=np.uint32)
    n = 1 + (len(samples) - FRAME) // HOP
    idx = np.arange(FRAME)[None, :] + HOP * np.arange(n)[:, None]
    win = samples[idx] * np.hanning(FRAME)[None, :]
    spec = np.abs(np.fft.rfft(win, axis=1)) ** 2
    edges = _band_edges()
    energy = np.stack([spec[:, edges[b] : max(edges[b] + 1, edges[b + 1])].sum(axis=1) for b in range(N_BANDS)], axis=1)
    d = (energy[1:, :-1] - energy[1:, 1:]) - (energy[:-1, :-1] - energy[:-1, 1:])
    bits = (d > 0).astype(np.uint32)
    weights = (1 << np.arange(32, dtype=np.uint32))
    out = (bits * weights).sum(axis=1).astype(np.uint32)
    return np.concatenate([[0], out]).astype(np.uint32)


def per_second(bits: np.ndarray) -> np.ndarray:
    """(n_sec, FRAMES_PER_SEC) uint32 blocks, one per whole second."""
    n = len(bits) // FRAMES_PER_SEC
    return bits[: n * FRAMES_PER_SEC].reshape(n, FRAMES_PER_SEC)


_POP = np.array([bin(i).count("1") for i in range(256)], dtype=np.uint8)


def _popcount(x: np.ndarray) -> np.ndarray:
    x = x.astype(np.uint32)
    return (_POP[x & 0xFF] + _POP[(x >> 8) & 0xFF] + _POP[(x >> 16) & 0xFF] + _POP[x >> 24]).astype(np.int32)


def ber(a: np.ndarray, b: np.ndarray) -> float:
    return float(_popcount(a ^ b).sum()) / (a.size * 32)


@dataclass
class AdLibrary:
    blocks: np.ndarray  # (n, FRAMES_PER_SEC) uint32
    max_ber: float = 0.25

    @classmethod
    def empty(cls) -> "AdLibrary":
        return cls(np.zeros((0, FRAMES_PER_SEC), dtype=np.uint32))

    @classmethod
    def load(cls, path: Path) -> "AdLibrary":
        if not path.exists():
            return cls.empty()
        return cls(np.load(path)["blocks"])

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(path, blocks=self.blocks)

    def add(self, blocks: np.ndarray) -> None:
        if len(blocks):
            self.blocks = np.concatenate([self.blocks, blocks.astype(np.uint32)])

    def _index(self) -> dict[int, list[tuple[int, int]]]:
        idx: dict[int, list[tuple[int, int]]] = {}
        for b, block in enumerate(self.blocks):
            for f, v in enumerate(block):
                if v:
                    idx.setdefault(int(v), []).append((b, f))
        return idx

    def match(self, bits: np.ndarray) -> list[bool]:
        """Per second of `bits`: does it line up with a stored ad second?

        Exact sub-fingerprint lookups propose an alignment; the candidate is
        accepted when the whole aligned second has BER < max_ber.
        """
        L = FRAMES_PER_SEC
        n_sec = len(bits) // L
        hits = [False] * n_sec
        if not len(self.blocks):
            return hits
        idx = self._index()
        for q, v in enumerate(bits):
            for b, f in idx.get(int(v), ()):
                lo = q - f  # query frame where the library second would start
                if lo < 0 or lo + L > len(bits):
                    continue
                if ber(bits[lo : lo + L], self.blocks[b]) < self.max_ber:
                    for s in {lo // L, (lo + L - 1) // L}:
                        if s < n_sec:
                            hits[s] = True
        return hits


def learn(library: AdLibrary, bits: np.ndarray, ad_secs: list[int]) -> int:
    """Add the given seconds (indices into `bits` seconds) to the library."""
    blocks = per_second(bits)
    pick = [s for s in ad_secs if 0 <= s < len(blocks)]
    library.add(blocks[pick])
    return len(pick)
