"""Graphical ROI calibration page (FR-17)."""

from newsbar.calibrate_ui import REGIONS, render

PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")


def test_page_embeds_frame_and_existing_boxes(tmp_path):
    (tmp_path / "f.png").write_bytes(PNG)
    page = render(tmp_path / "f.png", "tvbs", {"main_bar": [0.245, 0.805, 0.73, 0.095], "name": "x"},
                  tmp_path / "roi.html").read_text(encoding="utf-8")
    assert "data:image/png;base64," in page
    assert '"main_bar": [0.245, 0.805, 0.73, 0.095]' in page and '"name"' not in page
    assert all(f'data-r="{r}"' in page for r in REGIONS)
