"""Synthetic framed-ad video → visual signals → full `newsbar run` → sheet.

0–50 s  normal news, blue bar "A", logo top-right
50–90 s framed ad: right panel (vertical edge at 0.65), yellow bar still showing "A",
        logo covered, 0.5 s black + silence at 50 s
90–150 s back to news, blue bar "B"
"""

import json
import shutil
import sys
from pathlib import Path

import pytest
import yaml
from conftest import build_template

pytestmark = pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("ffmpeg"), reason="needs macOS + ffmpeg")

FONT = "/System/Library/Fonts/STHeiti Medium.ttc"
A = "新北土城七旬翁 遭貨車輾亡"
B = "高金素梅共諜案起訴 求刑14年"
CFG = {
    "name": "TVBS-N", "excel_column_header": "TVBS-N",
    "main_bar": [0.0, 0.80, 1.0, 0.10], "ticker": [0.0, 0.92, 1.0, 0.08],
    "logo": [0.86, 0.03, 0.10, 0.08], "main_video": [0.0, 0.0, 1.0, 0.78], "bar_color": "#1d3f8f",
}


def make_framed_video(tmp: Path) -> Path:
    import subprocess

    (tmp / "a.txt").write_text(A, encoding="utf-8")
    (tmp / "b.txt").write_text(B, encoding="utf-8")
    (tmp / "logo.txt").write_text("TV", encoding="utf-8")
    f = FONT.replace(":", r"\:")
    ad = "between(t,50,90)"
    vf = ",".join([
        f"drawbox=x=iw*0.65:y=0:w=iw*0.35:h=ih*0.80:color=0x202020:t=fill:enable='{ad}'",
        f"drawbox=x=0:y=ih*0.80:w=iw:h=ih*0.10:color=0x1d3f8f:t=fill:enable='not({ad})'",
        f"drawbox=x=0:y=ih*0.80:w=iw:h=ih*0.10:color=0xf2c200:t=fill:enable='{ad}'",
        f"drawtext=fontfile='{f}':textfile={tmp/'a.txt'}:fontcolor=white:fontsize=48:x=40:y=h*0.815:enable='lt(t,90)'",
        f"drawtext=fontfile='{f}':textfile={tmp/'b.txt'}:fontcolor=white:fontsize=48:x=40:y=h*0.815:enable='gte(t,90)'",
        f"drawbox=x=iw*0.86:y=ih*0.03:w=iw*0.10:h=ih*0.08:color=white:t=fill:enable='not({ad})'",
        f"drawtext=fontfile='{f}':textfile={tmp/'logo.txt'}:fontcolor=black:fontsize=36:x=w*0.885:y=h*0.045:enable='not({ad})'",
        "drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill:enable='between(t,50,50.5)'",
    ])
    out = tmp / "framed.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "gradients=s=1280x720:speed=0.01:d=150:r=10",
                    "-f", "lavfi", "-i", "sine=f=440:d=150",
                    "-vf", vf, "-af", "volume=enable='between(t,50,50.5)':volume=0",
                    "-pix_fmt", "yuv420p", "-shortest", str(out)], check=True)
    return out


@pytest.fixture(scope="module")
def video(tmp_path_factory):
    return make_framed_video(tmp_path_factory.mktemp("framed"))


def test_visual_signals(video):
    from newsbar.visual import analyse

    sig = analyse(video, CFG)
    by = {s.sec: s for s in sig}
    for t in (10, 30, 120):
        assert not by[t].layout_changed and not by[t].bar_color_changed and not by[t].logo_missing, by[t].debug
    for t in (55, 70, 85):
        assert by[t].layout_changed and by[t].bar_color_changed and by[t].logo_missing, by[t].debug
    assert by[50].black
    assert not by[30].black


def test_full_run_writes_ad_and_resumes(video, tmp_path):
    pytest.importorskip("Vision")
    from newsbar.cli import main

    cfg = tmp_path / "ch.yaml"
    cfg.write_text(yaml.safe_dump({"channels": {"tvbs": CFG}}, allow_unicode=True), encoding="utf-8")
    tpl = build_template(tmp_path / "tpl.xlsx")
    out = tmp_path / "out" / "12監看_20261106.xlsx"
    args = ["run", "--video", f"tvbs={video}@12:00:00", "--template", str(tpl), "--out", str(out),
            "--channels", str(cfg), "--workdir", str(tmp_path / "work")]
    assert main(args) == 0

    from newsbar.evaluate import read_sheet
    v = read_sheet(out, {"tvbs": "TVBS-N"})["tvbs"]
    assert v["1200"].endswith("/廣告"), v
    assert "1201" in v and "高金" in v["1201"], v
    assert set(v) == {"1200", "1201"}, v

    rep = json.loads(out.with_suffix(".report.json").read_text(encoding="utf-8"))
    assert rep["channels"]["tvbs"]["ads"] == 1
    assert out.with_suffix(".review.html").exists()

    # NFR-13: rerun reuses obs.jsonl and gives the same sheet
    out2 = tmp_path / "out" / "rerun.xlsx"
    assert main(args[:5] + ["--out", str(out2)] + args[7:] + ["--resume"]) == 0
    assert read_sheet(out2, {"tvbs": "TVBS-N"})["tvbs"] == v
