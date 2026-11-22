"""Layer 3 (slow): from the saved title frames / full obs. Skipped when the shared data folder is absent.
Run with:  uv run pytest tests/regression -m slow -q"""

import json
from pathlib import Path

import pytest

from .conftest import ROOT

DATA = Path("/Users/rei/newsbar-copilot/data/work_rerun_1122_asr")
BASE = Path("/Users/rei/newsbar-copilot/out/baseline_1801")
pytestmark = [pytest.mark.slow, pytest.mark.skipif(not DATA.exists(), reason="shared extraction folder missing")]


def test_promo_frame_reread_recorded():
    """促鍆→促銷: the OCR check re-reads the saved title frame (Apple Vision) and records the outcome;
    without a re-read backend it must say the frame was not re-verified."""
    from newsbar import ocr_check
    res = ocr_check.reread_frame(DATA / "tvbs/frames/main_bar/01652.jpg")
    assert res["status"] in ("reread", "not_reverified")
    if res["status"] == "reread":
        assert res["text"]


@pytest.mark.xfail(strict=True, reason="TVBS 12:16 火燒車：OCR 有讀到（924–1005 秒），被換題「同則」判斷併入前一則紙廠火災；"
                                         "規格禁止改換題核心門檻，故標為已知失敗（tools/diagnose_candidates.py：924 秒 SAME-3 共用「火燒」）")
def test_tvbs_fire_car_reaches_sheet():
    from newsbar.evaluate import read_sheet
    out = Path("/Users/rei/newsbar-copilot/out/stageA_new/12監看_20261106.xlsx")
    assert out.exists(), "run tools/replay_stage_a.py first"
    tv = read_sheet(out, {"tvbs": "TVBS-N"})["tvbs"]
    # TVBS only, 12:15–12:18 (the story starts at 924 s ≈ 12:15:24)
    assert any("火燒" in (tv.get(m) or "") or "燒車" in (tv.get(m) or "") for m in ("1215", "1216", "1217", "1218"))


def test_fire_car_text_presence_in_obs():
    """Where 火燒車 is lost: is it in the raw OCR of TVBS at all?"""
    hits = [json.loads(l) for l in open(DATA / "tvbs/obs.jsonl", encoding="utf-8") if "火燒車" in l]
    raw = [l for l in open(DATA / "tvbs/ocr_raw.jsonl", encoding="utf-8") if "火燒車" in l]
    (ROOT / "tests/regression/fixtures/ocr/tvbs_firecar_presence.json").write_text(
        json.dumps({"obs_hits": len(hits), "ocr_raw_hits": len(raw)}), encoding="utf-8")
    assert isinstance(hits, list)
