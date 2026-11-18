"""On-screen clock calibration (FR-24)."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from newsbar.clock import find_flip, parse_hhmm

FONT = "/System/Library/Fonts/STHeiti Medium.ttc"


def test_parse_hhmm():
    assert parse_hhmm("12:03") == 12 * 60 + 3
    assert parse_hhmm(" 11：59 ") == 11 * 60 + 59
    assert parse_hhmm("台北 22°C") is None


def test_find_flip_ignores_noise():
    r = [719] * 5 + [None, 720, 719] + [719] * 3 + [720] * 5  # a one-frame misread before the real flip
    assert find_flip(r) == (11, 720)


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("ffmpeg") or not Path(FONT).exists(), reason="macOS")
def test_start_clock_from_video(tmp_path):
    pytest.importorskip("Vision")
    from newsbar.clock import start_clock
    from newsbar.ocr.vision_backend import VisionBackend

    (tmp_path / "a.txt").write_text("11:59")
    (tmp_path / "b.txt").write_text("12:00")
    f = FONT.replace(":", r"\:")
    vf = (f"drawtext=fontfile='{f}':textfile={tmp_path/'a.txt'}:fontcolor=white:fontsize=60:x=40:y=h-90:enable='lt(t,23)',"
          f"drawtext=fontfile='{f}':textfile={tmp_path/'b.txt'}:fontcolor=white:fontsize=60:x=40:y=h-90:enable='gte(t,23)'")
    v = tmp_path / "c.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=1280x720:d=40:r=5",
                    "-vf", vf, "-pix_fmt", "yuv420p", str(v)], check=True)
    assert start_clock(v, [0.0, 0.84, 0.3, 0.14], VisionBackend(), probe_sec=40) == "11:59:37"
