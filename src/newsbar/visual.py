"""Picture & sound signals per second (architecture §4.4; spec AD-a … AD-d).

One ffmpeg pass streams 1-fps thumbnails through a pipe (no frames on disk);
a second pass runs ffmpeg's blackdetect / silencedetect for sub-second breaks.

Signals:
  layout_changed     AD-a  a persistent full-height vertical edge inside the
                           main picture (framed ad: picture shrinks to a box)
  bar_color_changed  AD-b  main_bar background far from the configured colour
  logo_missing       AD-c  logo ROI no longer matches the channel's usual logo
  black              AD-d  black frame or a short silence inside that second
  shot_cut                 big picture change from the previous second
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field

import numpy as np

from .ingest import _ffbin

TW, TH = 320, 180  # thumbnail size


@dataclass(frozen=True)
class VisualParams:
    split_x_range: tuple[float, float] = (0.45, 0.9)  # where a framed-ad edge may sit
    edge_min_height: float = 0.7  # fraction of picture height the edge must span
    edge_strength: float = 25.0  # |dI/dx| a pixel needs to count as edge
    framed_corr_min: float = 0.6  # NCC with the channel's framed-ad template
    anchor_corr_min: float = 0.6  # NCC with one of the channel's anchor-shot templates
    edge_persist_sec: int = 3
    bar_color_dist: float = 80.0  # RGB distance from configured bar colour
    logo_corr_min: float = 0.5
    shot_cut_diff: float = 40.0
    black_pix_th: float = 0.10
    silence_db: float = -50.0
    silence_min: float = 0.2
    silence_max: float = 1.0


@dataclass
class SecondSignals:
    sec: int
    layout_changed: bool = False
    bar_color_changed: bool = False
    logo_missing: bool = False
    black: bool = False
    shot_cut: bool = False
    anchor_shot: bool = False
    debug: dict = field(default_factory=dict)


def hex_rgb(h: str) -> np.ndarray:
    h = h.lstrip("#")
    return np.array([int(h[i : i + 2], 16) for i in (0, 2, 4)], dtype=np.float32)


def _box(img: np.ndarray, rel: list[float]) -> np.ndarray:
    h, w = img.shape[:2]
    x, y, bw, bh = rel
    return img[int(y * h) : max(int(y * h) + 1, int((y + bh) * h)), int(x * w) : max(int(x * w) + 1, int((x + bw) * w))]


def iter_thumbs(video, start: float = 0.0, duration: float | None = None):
    cmd = [_ffbin("ffmpeg"), "-hide_banner", "-loglevel", "error"]
    if start:
        cmd += ["-ss", str(start)]
    cmd += ["-i", str(video)]
    if duration:
        cmd += ["-t", str(duration)]
    cmd += ["-vf", f"fps=1,scale={TW}:{TH}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    size = TW * TH * 3
    try:
        while True:
            buf = p.stdout.read(size)  # type: ignore[union-attr]
            if len(buf) < size:
                break
            yield np.frombuffer(buf, dtype=np.uint8).reshape(TH, TW, 3)
    finally:
        p.stdout.close()  # type: ignore[union-attr]
        p.wait()


def vertical_edge(img: np.ndarray, p: VisualParams, main_video: list[float] | None = None) -> float:
    """Strongest straight vertical edge (as x fraction of the frame), or -1."""
    gray = img.astype(np.float32).mean(axis=2)
    region = gray
    x0 = 0
    if main_video:
        h, w = gray.shape
        x, y, bw, bh = main_video
        region = gray[int(y * h) : int((y + bh) * h), int(x * w) : int((x + bw) * w)]
        x0 = int(x * w)
    dx = np.abs(np.diff(region, axis=1))
    strong = dx > p.edge_strength
    coverage = strong.mean(axis=0)  # fraction of rows with a strong edge per column
    w = gray.shape[1]
    lo, hi = int(p.split_x_range[0] * w) - x0, int(p.split_x_range[1] * w) - x0
    lo, hi = max(lo, 0), min(hi, coverage.size)
    if hi <= lo:
        return -1.0
    col = lo + int(np.argmax(coverage[lo:hi]))
    if coverage[col] >= p.edge_min_height:
        return (col + x0 + 1) / w
    return -1.0


def _ncc(a: np.ndarray, b: np.ndarray) -> float:
    a = a.astype(np.float32).ravel()
    b = b.astype(np.float32).ravel()
    a -= a.mean()
    b -= b.mean()
    d = np.sqrt((a * a).sum() * (b * b).sum())
    return float((a * b).sum() / d) if d > 1e-6 else 0.0


def _intervals(stderr: str, start_key: str, end_key: str) -> list[tuple[float, float]]:
    starts = [float(x) for x in re.findall(rf"{start_key}:\s*([\d.]+)", stderr)]
    ends = [float(x) for x in re.findall(rf"{end_key}:\s*([\d.]+)", stderr)]
    return list(zip(starts, ends + [float("inf")] * (len(starts) - len(ends))))


def breaks(video, p: VisualParams = VisualParams(), start: float = 0.0, duration: float | None = None):
    """(black intervals, short-silence intervals) in video seconds."""
    cmd = [_ffbin("ffmpeg"), "-hide_banner", "-nostats"]
    if start:
        cmd += ["-ss", str(start)]
    cmd += ["-i", str(video)]
    if duration:
        cmd += ["-t", str(duration)]
    cmd += ["-vf", f"blackdetect=d=0.1:pix_th={p.black_pix_th}",
            "-af", f"silencedetect=n={p.silence_db}dB:d={p.silence_min}", "-f", "null", "-"]
    err = subprocess.run(cmd, capture_output=True, text=True).stderr
    black = _intervals(err, "black_start", "black_end")
    silence = [(a, b) for a, b in _intervals(err, "silence_start", "silence_end") if b - a <= p.silence_max]
    return black, silence


def analyse(
    video,
    channel_cfg: dict,
    p: VisualParams = VisualParams(),
    sec_offset: int = 0,
    start: float = 0.0,
    duration: float | None = None,
) -> list[SecondSignals]:
    bar_ref = hex_rgb(channel_cfg["bar_color"]) if channel_cfg.get("bar_color") else None
    framed_ref = None
    if channel_cfg.get("framed_ref") and channel_cfg.get("framed_roi"):
        framed_ref = np.load(channel_cfg["framed_ref"])
    anchor_refs = [np.load(p) for p in channel_cfg.get("anchor_refs", [])] if channel_cfg.get("anchor_roi") else []
    out: list[SecondSignals] = []
    logos: list[np.ndarray] = []
    prev: np.ndarray | None = None
    edges: list[float] = []
    for i, img in enumerate(iter_thumbs(video, start, duration)):
        s = SecondSignals(sec=i + sec_offset)
        edges.append(vertical_edge(img, p, channel_cfg.get("main_video")))
        if framed_ref is not None:
            corr = _ncc(_box(img, channel_cfg["framed_roi"]), framed_ref)
            s.debug["framed_corr"] = round(corr, 2)
            if corr >= p.framed_corr_min:
                s.layout_changed = True
        if anchor_refs:  # NEW-2b: studio/anchor-desk shot, one template per set-up
            box = _box(img, channel_cfg["anchor_roi"])
            corr = max(_ncc(box, ref) for ref in anchor_refs)
            s.debug["anchor_corr"] = round(corr, 2)
            s.anchor_shot = corr >= p.anchor_corr_min
        if bar_ref is not None and channel_cfg.get("main_bar"):
            bar = _box(img, channel_cfg["main_bar"]).reshape(-1, 3).astype(np.float32)
            bg = np.median(bar, axis=0)
            dist = float(np.linalg.norm(bg - bar_ref))
            s.bar_color_changed = dist > p.bar_color_dist
            s.debug["bar_dist"] = round(dist, 1)
        if channel_cfg.get("logo"):
            logos.append(_box(img, channel_cfg["logo"]).copy())
        if prev is not None:
            diff = float(np.abs(img.astype(np.int16) - prev.astype(np.int16)).mean())
            s.shot_cut = diff > p.shot_cut_diff
            s.debug["diff"] = round(diff, 1)
        prev = img
        out.append(s)

    # AD-a: the edge must stay at (about) the same x for edge_persist_sec seconds.
    for i, s in enumerate(out):
        win = edges[max(0, i - p.edge_persist_sec + 1) : i + 1]
        win2 = edges[i : i + p.edge_persist_sec]
        for w in (win, win2):
            if len(w) == p.edge_persist_sec and all(e > 0 for e in w) and max(w) - min(w) < 0.03:
                s.layout_changed = True
        s.debug["edge_x"] = round(edges[i], 3)

    # AD-c: the usual logo is the per-pixel median over the whole stream.
    if logos:
        ref = np.median(np.stack(logos), axis=0)
        for s, lg in zip(out, logos):
            corr = _ncc(lg, ref)
            s.logo_missing = corr < p.logo_corr_min
            s.debug["logo_corr"] = round(corr, 2)

    # AD-d: black frames (any length) and short silences.
    black, silence = breaks(video, p, start, duration)
    for a, b in black + silence:
        for s in out:
            t = s.sec - sec_offset
            if t + 1 > a and t < b:
                s.black = True
    return out


def save_framed_template(video, at: float, roi: list[float], out) -> np.ndarray:
    """Calibration: store the framed-ad layout region at second `at` (AD-a template)."""
    img = next(iter_thumbs(video, start=at, duration=1.5))
    ref = _box(img, roi).copy()
    np.save(out, ref)
    return ref


def merge(obs: list, signals: list[SecondSignals]) -> list:
    """Fill visual fields of SecondObs in place (matched by sec)."""
    by_sec = {s.sec: s for s in signals}
    for o in obs:
        s = by_sec.get(o.sec)
        if s is None:
            continue
        o.layout_changed = s.layout_changed
        o.bar_color_changed = s.bar_color_changed
        o.logo_missing = s.logo_missing
        o.black = s.black
        o.shot_cut = s.shot_cut
        o.anchor_shot = s.anchor_shot
    return obs
