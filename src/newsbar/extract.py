"""Video → per-second observations for one channel (OCR + picture + optional ASR).

Writes {workdir}/{channel}/obs.jsonl so later stages can be rerun alone (FR-20).
"""

from __future__ import annotations

import json
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
) -> tuple[list[SecondObs], list[str]]:
    work = workdir / channel
    rois = {k: v for k, v in cfg.items() if k in ("main_bar", "ticker")}
    log(f"[{channel}] 裁切 ROI …")
    dirs = ingest.extract_rois(video, rois, work / "frames", start=start, duration=duration)
    log(f"[{channel}] OCR …")
    res = ocr_channel(backend, dirs, sec_offset)
    save_jsonl(res.raw, work / "ocr_raw.jsonl")
    save_jsonl(res.voted, work / "ocr.jsonl")
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

        attach(obs, asr, video, sec_offset, start, duration)
    save_obs(obs, work / "obs.jsonl")
    return obs, res.warnings
