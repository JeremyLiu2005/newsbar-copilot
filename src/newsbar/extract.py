"""Video → per-second observations for one channel (OCR + picture + optional ASR).

Writes {workdir}/{channel}/obs.jsonl so later stages can be rerun alone (FR-20).
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import asdict, fields
from pathlib import Path

from . import ingest, visual
from .ocr import OcrBackend
from .ocr.stream import ocr_channel, save_jsonl
from .schemas import SecondObs


def save_obs(obs: list[SecondObs], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for o in obs:
            f.write(json.dumps(asdict(o), ensure_ascii=False) + "\n")


def load_obs(path: Path) -> list[SecondObs]:
    names = {f.name for f in fields(SecondObs)}
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            d = json.loads(line)
            out.append(SecondObs(**{k: v for k, v in d.items() if k in names}))
    return out


def add_clock(obs: list[SecondObs], video: Path, cfg: dict, backend: OcrBackend, workdir: Path,
              sec_offset: int = 0, start: float = 0.0, duration: float | None = None, every: int = 5) -> int:
    """Fill clock_ok (AD-j) by reading the clock ROI every `every` seconds."""
    if not cfg.get("clock"):
        return 0
    from .clock import parse_hhmm
    from .ocr.stream import read_dir

    dirs = ingest.extract_rois(video, {"main_bar": cfg["clock"]}, workdir / "clock_frames", start=start, duration=duration)
    reads, calls = read_dir(backend, dirs["main_bar"], sec_offset, every=every)
    import re

    date = re.compile(r"\d{1,2}/\d{1,2}")  # 11/06 FTV alternates the clock with the date
    ok = {r.sec: parse_hhmm(r.text) is not None or bool(r.text and date.search(r.text)) for r in reads}
    for o in obs:
        if o.sec in ok:
            o.clock_ok = ok[o.sec]
    return calls


def extract_channel(
    video: Path,
    channel: str,
    cfg: dict,
    backend: OcrBackend,
    workdir: Path,
    sec_offset: int = 0,
    asr=None,
    ad_library=None,
    start: float = 0.0,
    duration: float | None = None,
    log=print,
    asr_parallel: int = 1,
) -> tuple[list[SecondObs], list[str]]:
    work = workdir / channel
    rois = {k: v for k, v in cfg.items() if k in ("main_bar", "ticker", "alt_bar")}
    log(f"[{channel}] 裁切 ROI …")
    dirs = ingest.extract_rois(video, rois, work / "frames", start=start, duration=duration)
    log(f"[{channel}] OCR …")
    res = ocr_channel(backend, dirs, sec_offset)
    save_jsonl(res.raw, work / "ocr_raw.jsonl")
    save_jsonl(res.voted, work / "ocr.jsonl")
    log(f"[{channel}] 畫面時鐘 …")
    add_clock(res.obs, video, cfg, backend, work, sec_offset, start, duration)
    log(f"[{channel}] 畫面訊號 …")
    sig = visual.analyse(video, cfg, sec_offset=sec_offset, start=start, duration=duration)
    obs = visual.merge(res.obs, sig)
    log(f"[{channel}] 聲音指紋 …")
    import numpy as np

    from . import fingerprint

    bits = fingerprint.frame_bits(fingerprint.audio_samples(video, start, duration))
    np.save(work / "audio_fp.npy", bits)
    if ad_library is not None and len(ad_library.blocks):
        by_sec = {o.sec: o for o in obs}
        for s, hit in enumerate(ad_library.match(bits)):
            o = by_sec.get(s + sec_offset)
            if o is not None and hit:
                o.fingerprint_hit = True
    if asr is not None:
        log(f"[{channel}] 語音轉錄 …")
        from .asr import attach

        # At most `asr_parallel` channels transcribe at once. On a Mac this is 1: four Whisper
        # processes on one Apple GPU slowed each other down so much that a 2.5-min-per-channel job
        # took 33 min (11/06 fast run). An NVIDIA GPU can run several; size it to the card's memory.
        with asr_slot(workdir, asr_parallel):
            attach(obs, asr, video, sec_offset, start, duration)
    save_obs(obs, work / "obs.jsonl")
    return obs, res.warnings


def default_asr_parallel(platform: str | None = None) -> int:
    """1 on a Mac (shared Apple GPU); 2 elsewhere, a safe start for an 8 GB NVIDIA card."""
    import sys

    return 1 if (platform or sys.platform) == "darwin" else 2


@contextmanager
def asr_slot(workdir: Path, slots: int, poll: float = 1.0):
    """Hold one of `slots` lock files (workdir/.asr.lock.N) while transcribing.

    Works across the spawned per-channel processes. slots <= 0 means no limit; on systems
    without fcntl (Windows) the limit is skipped.
    """
    if slots <= 0:
        yield
        return
    try:
        import fcntl
    except ImportError:  # pragma: no cover - Windows
        yield
        return
    import time

    workdir.mkdir(parents=True, exist_ok=True)
    while True:
        for i in range(slots):
            f = open(workdir / f".asr.lock.{i}", "w")
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                f.close()
                continue
            try:
                yield i
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
                f.close()
            return
        time.sleep(poll)
