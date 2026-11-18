from __future__ import annotations

from pathlib import Path

import pytest
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from newsbar.schemas import SecondObs

CHANNEL_HEADERS = {"tvbs": "TVBS-N", "ettv": "東森", "ftv": "民視", "cts": "華視"}
ALL_CHANNELS = ["三立", "TVBS-N", "東森", "民視", "華視"]


def build_template(path: Path, extra_header_rows: int = 0, prefill: dict | None = None) -> Path:
    """Synthetic 12監看 sheet mirroring the 10/26 layout."""
    wb = Workbook()
    ws = wb.active
    ws.title = "12監看"
    r = 1
    ws.cell(r, 1, "12監看 監看日期：")
    ws.cell(r, 3, "2026/11/06")
    ws.cell(r, 6, "時段：12午間新聞 監看人員：")
    r += 1
    for _ in range(extra_header_rows):
        ws.cell(r, 1, "備註")
        r += 1
    for i, name in enumerate(ALL_CHANNELS):
        base = 2 + i * 4
        ws.cell(r, base, name).font = Font(bold=True)
        for k in range(1, 4):
            ws.cell(r, base + k, "收視率")
    r += 1
    ws.cell(r, 1, "主播")
    r += 1
    ws.cell(r, 1, "時間").fill = PatternFill("solid", fgColor="FFFF00")
    for i in range(len(ALL_CHANNELS)):
        base = 2 + i * 4
        for k, h in enumerate(["五字標", "A30", "M30", "A4+"]):
            ws.cell(r, base + k, h).alignment = Alignment(horizontal="center")
    header_row = r
    for m in range(60):
        row = header_row + 1 + m
        ws.cell(row, 1, 1200 + m)
        for i in range(len(ALL_CHANNELS)):
            base = 2 + i * 4
            for k in range(1, 4):
                c = ws.cell(row, base + k, round(0.3 + 0.01 * ((m + i + k) % 50), 2))
                c.number_format = "0.00"
    ws.cell(header_row + 3, 2, "三立內部記號1200")  # 三立欄 has content we must not touch
    ws.merge_cells(start_row=1, start_column=3, end_row=1, end_column=5)
    ws.column_dimensions["B"].width = 18
    for (minute, col_name), value in (prefill or {}).items():
        col = 2 + ALL_CHANNELS.index(col_name) * 4
        ws.cell(header_row + 1 + (minute - 1200), col, value)
    wb.save(path)
    return path


@pytest.fixture
def template(tmp_path: Path) -> Path:
    return build_template(tmp_path / "template.xlsx")


def stream(n: int, start: int = 0, **defaults) -> list[SecondObs]:
    """n seconds of identical observations."""
    return [SecondObs(sec=start + i, **defaults) for i in range(n)]


def news(n: int, start: int, bar: str, topic: str, asr: str = "主播說明", **kw) -> list[SecondObs]:
    kw.setdefault("speaker", "anchor")
    return stream(n, start, bar_text=bar, topic=topic, asr_text=asr, **kw)


def framed_ad(n: int, start: int, bar: str | None, **kw) -> list[SecondObs]:
    return stream(n, start, bar_text=bar, layout_changed=True, bar_color_changed=True,
                  asr_text="限時特價 現在打電話", ad_speech=True, **kw)


class FakeGenerator:
    """Returns scripted labels per attempt, regardless of source."""

    def __init__(self, *labels: str):
        self.labels = list(labels)
        self.calls = 0

    def generate(self, source, attempt):
        self.calls += 1
        return self.labels[min(attempt, len(self.labels) - 1)]


class EchoGenerator:
    """Uses the first 5 CJK chars of the bar text — always copy-only."""

    def generate(self, source, attempt):
        import re
        txt = "".join(re.findall(r"[一-鿿]", source.text))
        return txt[:5]
