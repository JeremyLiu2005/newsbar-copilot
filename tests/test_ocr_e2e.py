"""Synthetic news video → ffmpeg ROI crops → Apple Vision OCR → segments.

Skipped where ffmpeg, a CJK font or Vision is unavailable.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

FONT = next((p for p in ("/System/Library/Fonts/STHeiti Medium.ttc", "/System/Library/Fonts/Hiragino Sans GB.ttc")
             if Path(p).exists()), None)

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin" or not shutil.which("ffmpeg") or FONT is None,
    reason="needs macOS, ffmpeg and a CJK font",
)

TITLE_A = "新北土城七旬翁 遭貨車輾亡"
TITLE_B = "高金素梅共諜案起訴 求刑14年"
TICKER = "颱風動態最新消息 北部山區注意豪雨特報 今晚起影響全台各地 請民眾注意安全"

ROIS = {"main_bar": [0.0, 0.80, 1.0, 0.10], "ticker": [0.0, 0.92, 1.0, 0.08]}


def make_video(tmp: Path, seconds: int = 24, switch: int = 12) -> Path:
    for name, text in (("a", TITLE_A), ("b", TITLE_B), ("t", TICKER)):
        (tmp / f"{name}.txt").write_text(text, encoding="utf-8")
    f = FONT.replace(":", r"\:")
    vf = ",".join([
        "drawbox=x=0:y=ih*0.80:w=iw:h=ih*0.10:color=0x1d3f8f:t=fill",
        f"drawtext=fontfile='{f}':textfile={tmp/'a.txt'}:fontcolor=white:fontsize=48:x=40:y=h*0.815:enable='lt(t,{switch})'",
        f"drawtext=fontfile='{f}':textfile={tmp/'b.txt'}:fontcolor=white:fontsize=48:x=40:y=h*0.815:enable='gte(t,{switch})'",
        "drawbox=x=0:y=ih*0.92:w=iw:h=ih*0.08:color=black:t=fill",
        f"drawtext=fontfile='{f}':textfile={tmp/'t.txt'}:fontcolor=yellow:fontsize=30:x=w-mod(t*160\\,w+tw):y=h*0.935",
    ])
    out = tmp / "news.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"color=c=0x404040:s=1280x720:d={seconds}:r=10", "-vf", vf, "-pix_fmt", "yuv420p", str(out)],
                   check=True)
    return out


@pytest.fixture(scope="module")
def vision():
    pytest.importorskip("Vision")
    from newsbar.ocr.vision_backend import VisionBackend
    return VisionBackend()


def test_video_to_bar_stream_and_segments(tmp_path, vision):
    from newsbar import ingest
    from newsbar.ad_state import detect
    from newsbar.ocr.stream import normalize, ocr_channel
    from newsbar.segmenter import segment

    video = make_video(tmp_path)
    info = ingest.probe(video)
    assert info.width == 1280 and round(info.duration) == 24

    dirs = ingest.extract_rois(video, ROIS, tmp_path / "frames")
    assert len(list(dirs["main_bar"].glob("*.jpg"))) == 24

    res = ocr_channel(vision, dirs, sec_offset=-5)  # video starts at 11:59:55
    texts = [o.bar_text for o in res.obs]
    assert texts[2] == normalize(TITLE_A), res.raw[:3]
    assert texts[-2] == normalize(TITLE_B), res.raw[-3:]
    assert res.obs[0].sec == -5
    assert all(o.ticker_text != o.bar_text for o in res.obs)  # ticker kept separate (TXT-2)

    segs = segment(res.obs, detect(res.obs))
    assert len(segs) == 2
    assert segs[1].start == 12 - 5  # bar switch at video 12 s → sheet 7 s


def test_sheet_offset():
    from newsbar.ingest import sheet_offset
    assert sheet_offset("11:59:50") == -10
    assert sheet_offset("12:00:00") == 0
    assert sheet_offset("12:30", "1230") == 0
