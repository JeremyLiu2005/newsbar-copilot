"""Annotation helper: runs, page, gold cells, κ."""

from conftest import CHANNEL_HEADERS

from newsbar.annotate import clock, gold_cells, kappa, render, runs
from newsbar.minute_mapper import minute_labels
from newsbar.schemas import SecondObs


def obs(seq):
    out, t = [], -5
    for text, n in seq:
        for _ in range(n):
            out.append(SecondObs(sec=t, bar_text=text))
            t += 1
    return out


def test_runs_keep_items_three_seconds_or_longer():
    r = runs(obs([("標題甲", 20), ("短字幕", 2), ("名條甲乙", 3), (None, 10), ("標題乙", 30)]))
    assert [x["text"] for x in r] == ["標題甲", "名條甲乙", "（主 bar 無字）", "標題乙"]
    assert r[0]["start"] == -5 and clock(-5) == "11:59:55"


def test_page_has_no_system_decisions(tmp_path):
    page = render({"tvbs": obs([("標題甲", 20)])}, {"tvbs": "TVBS-N"}, tmp_path / "a.html").read_text(encoding="utf-8")
    assert "標題甲" in page and "新的一則" in page and "廣告開始" in page
    assert "NEW-1" not in page and "confidence" not in page


def test_gold_cells_and_kappa():
    items = [{"channel": "tvbs", "sec": 10, "kind": "news", "label": "土城翁輾亡"},
             {"channel": "tvbs", "sec": 130, "kind": "ad", "label": ""},
             {"channel": "tvbs", "sec": 300, "kind": "news", "label": "高金遭起訴"}]
    cells = gold_cells(items, CHANNEL_HEADERS)
    assert {m: c.value for m, c in cells["tvbs"].items()} == {"1200": "土城翁輾亡", "1202": "廣告", "1205": "高金遭起訴"}
    mins = minute_labels("1200", 60)
    a = {"tvbs": {"1200", "1205", "1210"}}
    assert kappa(a, a, mins) == 1.0
    assert 0 < kappa(a, {"tvbs": {"1200", "1205", "1230"}}, mins) < 1
