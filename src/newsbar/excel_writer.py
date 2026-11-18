"""Write minute cells back into the 12監看 template (spec §8).

The writer locates the time rows and channel columns by header text (XLS-3),
refuses to overwrite non-empty target cells (XLS-4), and proves that every
non-target cell kept its value and style (XLS-2).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.worksheet.worksheet import Worksheet

from .schemas import Cell

SHEET = "12監看"
TIME_HEADER = "時間"
LABEL_HEADER = "五字標"


class WriterError(RuntimeError):
    pass


@dataclass
class Layout:
    time_header_row: int
    time_col: int
    minute_rows: dict[str, int]  # "1200" → row
    channel_cols: dict[str, int]  # channel key → column of its 五字標


def _norm_minute(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    s = str(v).strip()
    return s if len(s) == 4 and s.isdigit() else None


def locate(ws: Worksheet, channel_headers: dict[str, str]) -> Layout:
    """channel_headers: channel key → header text in the channel row (e.g. 'TVBS-N')."""
    header = None
    for row in ws.iter_rows():
        for c in row:
            if isinstance(c.value, str) and c.value.strip() == TIME_HEADER:
                header = c
                break
        if header:
            break
    if header is None:
        raise WriterError(f"找不到「{TIME_HEADER}」表頭")
    hr, tc = header.row, header.column

    minute_rows: dict[str, int] = {}
    for r in range(hr + 1, ws.max_row + 1):
        m = _norm_minute(ws.cell(r, tc).value)
        if m:
            minute_rows[m] = r
    if not minute_rows:
        raise WriterError("時間欄下方沒有任何 HHMM 分鐘列")

    channel_cols: dict[str, int] = {}
    for key, title in channel_headers.items():
        hit = None
        for r in range(1, hr):
            for c in range(1, ws.max_column + 1):
                v = ws.cell(r, c).value
                if isinstance(v, str) and v.strip() == title:
                    hit = c
                    break
            if hit:
                break
        if hit is None:
            raise WriterError(f"找不到台別表頭「{title}」")
        if str(ws.cell(hr, hit).value or "").strip() != LABEL_HEADER:
            raise WriterError(f"台別「{title}」下方不是「{LABEL_HEADER}」欄")
        channel_cols[key] = hit
    return Layout(hr, tc, minute_rows, channel_cols)


def _norm_style(obj) -> tuple:
    """openpyxl writes boolean style flags back as None when they equal the
    default (False); treat the two as the same so a plain round-trip passes."""
    return tuple((k, False if v is None else v) for k, v in sorted(vars(obj).items()) if not k.startswith("_"))


def _fingerprint(ws: Worksheet, skip: set[tuple[int, int]]) -> str:
    h = hashlib.sha256()
    for row in ws.iter_rows():
        for c in row:
            if (c.row, c.column) in skip:
                continue
            h.update(
                repr(
                    (
                        c.coordinate,
                        c.value,
                        c.number_format,
                        c.font,
                        c.fill,
                        c.border,
                        _norm_style(c.alignment),
                        _norm_style(c.protection),
                    )
                ).encode()
            )
    h.update(repr(sorted(str(r) for r in ws.merged_cells.ranges)).encode())
    for key, dim in sorted(ws.column_dimensions.items()):
        h.update(repr((key, None if dim.width is None else round(dim.width, 4), dim.hidden)).encode())
    return h.hexdigest()


def write(
    template: Path,
    out: Path,
    cells: dict[str, dict[str, Cell]],
    channel_headers: dict[str, str],
    sheet: str = SHEET,
) -> Layout:
    wb = load_workbook(template)
    if sheet not in wb.sheetnames:
        raise WriterError(f"範本沒有「{sheet}」分頁")
    ws = wb[sheet]
    layout = locate(ws, channel_headers)

    targets = {(r, c) for r in layout.minute_rows.values() for c in layout.channel_cols.values()}
    before = _fingerprint(ws, targets)

    for ch, by_minute in cells.items():
        if ch not in layout.channel_cols:
            raise WriterError(f"台別 {ch!r} 不在寫入範圍")
        col = layout.channel_cols[ch]
        for minute, cell in by_minute.items():
            if minute not in layout.minute_rows:
                raise WriterError(f"範本沒有分鐘列 {minute}")
            target = ws.cell(layout.minute_rows[minute], col)
            if target.value not in (None, ""):
                raise WriterError(f"{target.coordinate} 已有內容「{target.value}」，不覆寫")
            target.value = cell.value

    if _fingerprint(ws, targets) != before:
        raise WriterError("非目標儲存格被改動")
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)

    # Re-open the saved file and check again (XLS-2).
    ws2 = load_workbook(out)[sheet]
    if _fingerprint(ws2, targets) != _fingerprint(load_workbook(template)[sheet], targets):
        raise WriterError("存檔後非目標儲存格與範本不一致")
    return layout
