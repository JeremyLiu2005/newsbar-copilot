"""spec §8 — T19–T21."""

import pytest
from conftest import CHANNEL_HEADERS, build_template
from openpyxl import load_workbook

from newsbar.excel_writer import WriterError, _fingerprint, locate, write
from newsbar.minute_mapper import map_events
from newsbar.schemas import Event, EventType as T


def sample_cells():
    evs = [Event("tvbs", 10, T.NEWS, "阿美麻糬亡"), Event("ettv", 65, T.NEWS, "扛單車砸人"),
           Event("ftv", 32 * 60, T.AD), Event("cts", 28 * 60 + 5, T.NEWS, "持刀警開槍"),
           Event("cts", 28 * 60 + 40, T.NEWS, "台股二補跌")]
    return map_events(evs, CHANNEL_HEADERS.keys())


def test_T19_only_target_cells_change(template, tmp_path):
    out = tmp_path / "out.xlsx"
    layout = write(template, out, sample_cells(), CHANNEL_HEADERS)
    ws_in = load_workbook(template)["12監看"]
    ws_out = load_workbook(out)["12監看"]
    targets = {(r, c) for r in layout.minute_rows.values() for c in layout.channel_cols.values()}
    assert _fingerprint(ws_in, targets) == _fingerprint(ws_out, targets)
    r = layout.minute_rows
    c = layout.channel_cols
    assert ws_out.cell(r["1200"], c["tvbs"]).value == "阿美麻糬亡"
    assert ws_out.cell(r["1201"], c["ettv"]).value == "扛單車砸人"
    assert ws_out.cell(r["1232"], c["ftv"]).value == "廣告"
    assert ws_out.cell(r["1228"], c["cts"]).value == "持刀警開槍/台股二補跌"
    assert ws_out.cell(r["1202"], 2).value == "三立內部記號1200"  # 三立欄 untouched
    assert ws_out.cell(r["1203"], c["tvbs"]).value is None


def test_T20_refuses_to_overwrite(tmp_path):
    tpl = build_template(tmp_path / "t.xlsx", prefill={(1200, "TVBS-N"): "舊資料"})
    with pytest.raises(WriterError, match="不覆寫"):
        write(tpl, tmp_path / "out.xlsx", sample_cells(), CHANNEL_HEADERS)
    assert not (tmp_path / "out.xlsx").exists()


def test_T21_extra_header_row_still_located(tmp_path):
    tpl = build_template(tmp_path / "t.xlsx", extra_header_rows=2)
    out = tmp_path / "out.xlsx"
    layout = write(tpl, out, sample_cells(), CHANNEL_HEADERS)
    ws = load_workbook(out)["12監看"]
    assert ws.cell(layout.minute_rows["1200"], 1).value == 1200
    assert ws.cell(layout.minute_rows["1200"], layout.channel_cols["tvbs"]).value == "阿美麻糬亡"


def test_columns_are_located_by_header_not_position(template):
    ws = load_workbook(template)["12監看"]
    layout = locate(ws, CHANNEL_HEADERS)
    assert layout.channel_cols == {"tvbs": 6, "ettv": 10, "ftv": 14, "cts": 18}


def test_missing_channel_header_fails(template):
    ws = load_workbook(template)["12監看"]
    with pytest.raises(WriterError, match="找不到台別"):
        locate(ws, {"x": "中視"})


def test_round_trip_normalisation_of_real_template_quirks(tmp_path):
    """11/06 template: wrapText=False comes back as None, widths lose float digits."""
    from openpyxl.styles import Alignment

    tpl = build_template(tmp_path / "t.xlsx")
    wb = load_workbook(tpl)
    ws = wb["12監看"]
    ws["F3"].alignment = Alignment(horizontal="left", vertical="center", wrap_text=False)
    ws.column_dimensions["B"].width = 18.290000915527344
    wb.save(tpl)
    write(tpl, tmp_path / "out.xlsx", sample_cells(), CHANNEL_HEADERS)  # must not raise
