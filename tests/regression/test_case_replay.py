"""Layer 2: replay the saved OCR / ASR of each real case through
OCR check → short title → warning level → minute mapping. This does NOT verify reading text from
the picture (that is layer 3, test_raw_frames.py)."""

from pathlib import Path

import pytest
import yaml

from newsbar import labeler, pipeline
from newsbar.minute_mapper import map_events
from newsbar.schemas import Event, EventType, Labels, SecondObs, Source

from .conftest import ROOT, load_case

RULES = yaml.safe_load((ROOT / "config" / "rules.1106.yaml").read_text(encoding="utf-8"))
RULES["labeler"]["generator"] = "rule"


def obs_of(case):
    """Per-second OCR reconstructed from the saved variant counts (same texts, same multiplicity)."""
    out, sec = [], case["source"]["start_sec"]
    for text, n in case["source"]["ocr_variants"]:
        for _ in range(n):
            out.append(SecondObs(sec=sec, bar_text=text, bar_conf=0.5))
            sec += 1
    return out


def decide(case_id):
    case = load_case(case_id)
    s = case["source"]
    return case, pipeline.decide_story(case["channel"], s["bar_texts"], s["asr_text"] or "", obs_of(case), None, RULES)


ALL = sorted(p.stem for p in (Path(__file__).parent / "cases").glob("*.json"))


@pytest.mark.parametrize("case_id", ALL)
def test_every_case_yields_a_usable_record(case_id):
    case, d = decide(case_id)
    lr = labeler.LabelRules.from_rules(RULES)
    assert d["label"] and lr.min_len <= labeler.label_length(d["label"]) <= lr.max_len or d["status"] == labeler.STATUS_PENDING
    assert d["review_level"] in (labeler.LEVEL_NONE, labeler.LEVEL_INFO, labeler.LEVEL_CHECK, labeler.LEVEL_FAIL)
    for chk in d["ocr"]:
        assert set(chk) >= {"ocr_raw", "ocr_corrected", "correction_reason", "supporting_frame_ids",
                            "supporting_asr_ids", "review_status"}
    for bad in case["expectation"]["forbidden_behavior"]:
        assert bad  # documented


@pytest.mark.parametrize("case_id", ["ettv_1200_taigu_cut", "tvbs_1251_france_open", "ftv_1209_detain_numfrag",
                                     "cts_1200_forklift_result"])
def test_old_wrong_label_not_reproduced(case_id):
    case, d = decide(case_id)
    assert d["label"] != case["old_output"]["value"]
    if d["status"] != labeler.STATUS_PENDING:
        assert labeler.validate(d["label"], labeler.LabelSource(case["source"]["bar_texts"]),
                                labeler.LabelRules.from_rules(RULES)) == []


def test_france_not_flagged_for_missing_place():
    _, d = decide("tvbs_1251_france_open")
    assert all("地點" not in n["reason"] for n in d["notes"])


def test_qianzhen_not_yellow_for_place():
    _, d = decide("ftv_1245_qianzhen_not_social")
    assert all("地點" not in n["reason"] or n["level"] == labeler.LEVEL_INFO for n in d["notes"])


def test_promo_ocr_error_not_in_label_and_raw_kept():
    _, d = decide("tvbs_1228_promo_ocr")
    assert "鍆" not in d["label"]
    chk = next(c for c in d["ocr"] if "促鍆" in c["ocr_raw"])
    assert chk["ocr_raw"].startswith("最大旅展促鍆")
    # either corrected with cited support, or left as a traceable doubt
    if chk["ocr_corrected"]:
        assert chk["supporting_frame_ids"] or chk["supporting_asr_ids"]
    else:
        assert chk["review_status"] == "待回查"


def test_fork_typo_not_guessed_without_support():
    _, d = decide("tvbs_1209_forklift_fork_ocr")
    chk = next(c for c in d["ocr"] if "牙叉" in c["ocr_raw"])
    assert chk["ocr_corrected"] is None and chk["review_status"] == "待回查"
    assert "牙叉" not in d["label"]


def test_unreadable_name_fragment_not_spliced():
    _, d = decide("cts_1206_name_unstable")
    assert "詭助" not in d["label"] and "偷瀆" not in d["label"]


@pytest.mark.xfail(strict=True, reason="張俊傑「偷瀆」「詭助」：同則標題只是改寫、無截圖重讀，"
                                         "程式無法自動判定為可疑字（已知未解，需 OCR-V4 重讀截圖）")
def test_unreadable_name_fragment_flagged():
    _, d = decide("cts_1206_name_unstable")
    # flagged because of the title's doubtful characters — not merely because the fallback was used
    assert any(n["level"] == labeler.LEVEL_CHECK and n["reason"].startswith("標題有可疑字") for n in d["notes"])


def test_rare_name_character_not_changed():
    _, d = decide("tvbs_1259_name_rare_char_kept")
    assert all(c["ocr_corrected"] is None for c in d["ocr"])


# --- minute mapping (MAP-3 = next in rules.1106) -------------------------------------------------

def _ev(sec, label, sid):
    return Event("tvbs", sec, EventType.NEWS, label, sid, "high", Source(None, None, None), {}, None, [])


LABELS = Labels.from_rules(RULES)


def test_change_on_the_minute_fills_that_minute():
    cells = map_events([_ev(0, "甲新聞", "a"), _ev(120, "乙新聞", "b")], ["tvbs"], "1200", 60, LABELS)["tvbs"]
    assert cells["1202"].value == "乙新聞"


def test_mid_minute_change_fills_next_minute_without_slash():
    cells = map_events([_ev(0, "甲新聞", "a"), _ev(125, "乙新聞", "b")], ["tvbs"], "1200", 60, LABELS)["tvbs"]
    assert "1202" not in cells or cells["1202"].value != "乙新聞"
    assert cells["1203"].value == "乙新聞"
    assert all("/" not in c.value for c in cells.values())


def test_change_inside_1259_never_writes_1300():
    cells = map_events([_ev(0, "甲新聞", "a"), _ev(59 * 60 + 30, "乙新聞", "b")], ["tvbs"], "1200", 60, LABELS)["tvbs"]
    assert "1300" not in cells and max(cells) <= "1259"
