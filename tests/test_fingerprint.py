"""Ad fingerprint library: learn on one stream, recognise in another (AD-f)."""

import numpy as np

from newsbar.fingerprint import SR, AdLibrary, frame_bits, learn

rng = np.random.default_rng(7)


def jingle(seconds: float) -> np.ndarray:
    t = np.arange(int(seconds * SR)) / SR
    tones = sum(np.sin(2 * np.pi * f * t) * (0.5 + 0.5 * np.sin(2 * np.pi * r * t))
                for f, r in ((440, 1.3), (660, 0.7), (990, 2.1), (1480, 0.4)))
    return (0.2 * tones).astype(np.float32)


def speech_like(seconds: float, seed: int) -> np.ndarray:
    r = np.random.default_rng(seed)
    noise = r.normal(0, 0.1, int(seconds * SR)).astype(np.float32)
    env = np.repeat(r.uniform(0.2, 1.0, int(seconds * 4) + 1), SR // 4)[: len(noise)]
    return noise * env


def test_same_ad_is_recognised_at_another_offset():
    ad = jingle(10)
    day1 = np.concatenate([speech_like(20, 1), ad, speech_like(20, 2)])
    day2 = np.concatenate([speech_like(33.4, 3), ad, speech_like(15, 4)])

    lib = AdLibrary.empty()
    assert learn(lib, frame_bits(day1), list(range(21, 29))) == 8

    hits = lib.match(frame_bits(day2))
    hit_secs = [i for i, h in enumerate(hits) if h]
    assert hit_secs, "ad not recognised"
    assert all(33 <= s <= 44 for s in hit_secs), hit_secs
    assert len(hit_secs) >= 5


def test_no_false_hits_on_other_audio():
    lib = AdLibrary.empty()
    learn(lib, frame_bits(np.concatenate([speech_like(5, 9), jingle(10)])), list(range(6, 14)))
    hits = lib.match(frame_bits(speech_like(60, 11)))
    assert not any(hits)


def test_library_round_trip(tmp_path):
    lib = AdLibrary.empty()
    learn(lib, frame_bits(jingle(5)), [1, 2, 3])
    lib.save(tmp_path / "ads.npz")
    assert AdLibrary.load(tmp_path / "ads.npz").blocks.shape == lib.blocks.shape
    assert len(AdLibrary.load(tmp_path / "missing.npz").blocks) == 0
