"""Video ingest: probe, clock alignment, per-second ROI crops (architecture §4.1).

Only the configured regions are written to disk (JPEG), one image per second,
so a 1-hour channel costs ~100 MB instead of full frames (NFR-08).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

THUMB_W, THUMB_H = 96, 12  # main_bar change detector (one byte per pixel)

ROI_NAMES = ("main_bar", "ticker", "flash", "weather_corner", "logo", "main_video")


class IngestError(RuntimeError):
    pass


@dataclass
class VideoInfo:
    path: Path
    duration: float
    width: int
    height: int
    fps: float


def _ffbin(name: str) -> str:
    p = shutil.which(name)
    if not p:
        raise IngestError(f"找不到 {name}，請先安裝 FFmpeg")
    return p


def probe(video: Path) -> VideoInfo:
    out = subprocess.run(
        [_ffbin("ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,r_frame_rate:format=duration", "-of", "json", str(video)],
        capture_output=True, text=True, check=True,
    ).stdout
    d = json.loads(out)
    st = d["streams"][0]
    num, den = st["r_frame_rate"].split("/")
    return VideoInfo(video, float(d["format"]["duration"]), int(st["width"]), int(st["height"]),
                     float(num) / float(den or 1))


def parse_clock(clock: str) -> int:
    """'11:59:50' → seconds since midnight."""
    parts = [int(p) for p in clock.split(":")]
    while len(parts) < 3:
        parts.append(0)
    h, m, s = parts
    return h * 3600 + m * 60 + s


def sheet_offset(video_clock_start: str, sheet_start: str = "1200") -> int:
    """Seconds to add to a video timestamp to get sheet-relative seconds (MAP-2)."""
    sheet = int(sheet_start[:2]) * 3600 + int(sheet_start[2:]) * 60
    return parse_clock(video_clock_start) - sheet


def _crop_expr(box: list[float]) -> str:
    x, y, w, h = box
    for v in box:
        if not 0 <= v <= 1:
            raise IngestError(f"ROI 座標必須是 0–1 的比例：{box}")
    if x + w > 1.0001 or y + h > 1.0001:
        raise IngestError(f"ROI 超出畫面：{box}")
    return f"crop=trunc(iw*{w}/2)*2:trunc(ih*{h}/2)*2:trunc(iw*{x}):trunc(ih*{y})"


def extract_rois(
    video: Path,
    rois: dict[str, list[float]],
    out_dir: Path,
    fps: float = 1.0,
    start: float = 0.0,
    duration: float | None = None,
    quality: int = 3,
) -> dict[str, Path]:
    """One ffmpeg pass; writes out_dir/{roi}/{index:05d}.jpg where index = second."""
    names = [n for n in rois if n in ROI_NAMES]
    if "main_bar" not in names:
        raise IngestError("設定檔缺少 main_bar ROI")
    out_dir.mkdir(parents=True, exist_ok=True)
    splits = "".join(f"[s{i}]" for i in range(len(names)))
    graph = [f"[0:v]fps={fps},split={len(names)}{splits}"]
    for i, n in enumerate(names):
        if n == "main_bar":  # extra tiny grayscale copy so OCR can skip unchanged seconds
            graph.append(f"[s{i}]{_crop_expr(rois[n])},split=2[o{i}][c{i}]")
            graph.append(f"[c{i}]scale={THUMB_W}:{THUMB_H},format=gray[t{i}]")
        else:
            graph.append(f"[s{i}]{_crop_expr(rois[n])}[o{i}]")
    cmd = [_ffbin("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y"]
    if start:
        cmd += ["-ss", str(start)]
    if duration:  # input option: an output -t would only limit the first of several outputs
        cmd += ["-t", str(duration)]
    cmd += ["-i", str(video)]
    cmd += ["-filter_complex", ";".join(graph)]
    dirs = {}
    for i, n in enumerate(names):
        d = out_dir / n
        d.mkdir(exist_ok=True)
        dirs[n] = d
        cmd += ["-map", f"[o{i}]", "-q:v", str(quality), "-start_number", "0", str(d / "%05d.jpg")]
        if n == "main_bar":
            cmd += ["-map", f"[t{i}]", "-f", "rawvideo", str(out_dir / "main_bar.thumbs")]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise IngestError(r.stderr.strip()[-500:])
    return dirs


def extract_frame(video: Path, at: float, out: Path) -> Path:
    """Full frame for ROI calibration (FR-17)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [_ffbin("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", "-ss", str(at), "-i", str(video),
         "-frames:v", "1", str(out)],
        check=True,
    )
    return out
