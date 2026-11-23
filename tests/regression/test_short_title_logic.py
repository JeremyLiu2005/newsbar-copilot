"""Layer 1: program logic with fixed model replies. Each defect test is paired with a normal case."""

import json

import pytest

from newsbar import labeler, llm

from .conftest import Scripted, load_case, source_of

RULES = labeler.LabelRules.from_rules({"labeler": {}})  # the shipped defaults (test version: 4–6)


def errs(label, case_id):
    return labeler.validate(label, source_of(load_case(case_id)), RULES)


def _visible_prefixes(case):
    out = set()
    for t in case["source"]["bar_texts"]:
        v = labeler._visible(labeler.strip_lead_fragment(t))
        out |= {v[:n] for n in range(3, 8)}
    return out


# --- length: configurable 4–6, a switch back to exactly 5 -----------------------------------

def test_default_length_range_is_four_to_six():
    assert (RULES.min_len, RULES.max_len) == (4, 6)


def test_exact_five_switch_still_works():
    r5 = labeler.LabelRules.from_rules({"labeler": {"min_len": 5, "max_len": 5}})
    src = source_of(load_case("cts_1200_forklift_result"))
    assert "LBL-5" in labeler.validate("堆高機撞婦亡", src, r5)


def test_six_char_event_title_not_rejected_for_length():
    assert errs("堆高機撞婦亡", "cts_1200_forklift_result") == []


# --- completeness: cut words, number fragments, unfinished clauses ---------------------------

@pytest.mark.parametrize("label,case_id", [
    ("台股盤中一", "ettv_1200_taigu_cut"),
    ("法國再陷入", "tvbs_1251_france_open"),
    ("拘提到案6", "ftv_1209_detain_numfrag"),
])
def test_incomplete_labels_rejected(label, case_id):
    assert errs(label, case_id), f"{label} must not pass"


@pytest.mark.parametrize("label,case_id", [
    ("3童過馬路", "tvbs_1210_three_kids_ok"),
    ("春風衛生紙", "ettv_1210_prefix_ok"),  # equals the title's first characters and is a whole phrase
    ("拘提到案", "ftv_1209_detain_numfrag"),
    ("6人聲押", "ftv_1209_detain_numfrag"),
])
def test_complete_labels_pass(label, case_id):
    assert errs(label, case_id) == []


def test_name_code_number_not_killed_by_number_rule():
    src = labeler.LabelSource(["小資抄底!0050爆量2300萬股投顧:AI長線不變"], "")
    assert not {"SEM-NUM", "LBL-9"} & set(labeler.validate("0050爆量股", src, RULES))


# --- event focus ---------------------------------------------------------------------------

def test_label_missing_main_result_rejected():
    assert "EVT-MISS" in errs("堆高機警示", "cts_1200_forklift_result")


def test_event_check_has_no_effect_without_result_words():
    assert "EVT-MISS" not in errs("春風衛生紙", "ettv_1210_prefix_ok")


# --- rewrite loop and fallback -------------------------------------------------------------

def test_bad_answer_triggers_rewrite_with_concrete_reason():
    gen = Scripted("拘提到案6", "6人聲押")
    res = labeler.make_label(source_of(load_case("ftv_1209_detain_numfrag")), gen, rules=RULES)
    assert res.label == "6人聲押" and res.fallback is None
    assert gen.calls[1][1], "the rewrite request must carry the reason"
    assert "6" in gen.calls[1][1]


def test_rewrites_are_capped_at_two():
    gen = Scripted("台股盤中一")
    labeler.make_label(source_of(load_case("ettv_1200_taigu_cut")), gen, rules=RULES)
    rewrites = [c for c in gen.calls if "只能從下列候選" not in (c[1] or "")]
    assert len(rewrites) == 3  # first answer + at most 2 rewrites
    assert len(gen.calls) <= 4  # plus at most one pick from the checked rule candidates (FB-4c)


@pytest.mark.parametrize("case_id", ["ettv_1200_taigu_cut", "tvbs_1251_france_open", "ftv_1209_detain_numfrag",
                                     "cts_1200_forklift_result", "ettv_1251_scam_unparsable"])
def test_fallback_never_cuts_title_prefix_or_words(case_id):
    case = load_case(case_id)
    src = source_of(case)
    gen = Scripted(case["old_output"]["value"])  # the model keeps repeating the old wrong answer
    res = labeler.make_label(src, gen, rules=RULES)
    assert res.label, "a label (or best candidate) must still be produced"
    assert res.label != case["old_output"]["value"]
    errors = labeler.validate(res.label, src, RULES)
    if errors:  # no valid label could be built: it must say so, never pass silently
        assert res.status == labeler.STATUS_PENDING and res.reasons
    else:
        assert not labeler.cut_words(res.label, src) and not labeler.orphan_units(res.label, src)
    # every character is from the source titles (nothing added)
    title_chars = set("".join(src.bar_texts))
    assert all(ch in title_chars or not labeler._CJK.match(ch) for ch in res.label)


def test_fallback_is_not_a_blind_prefix_cut():
    case = load_case("ettv_1200_taigu_cut")
    res = labeler.make_label(source_of(case), None, rules=RULES)
    # a prefix is acceptable only when it is itself a validated whole phrase
    if res.label in _visible_prefixes(case):
        assert labeler.validate(res.label, source_of(case), RULES) == []


def test_forklift_fallback_keeps_the_result():
    res = labeler.make_label(source_of(load_case("cts_1200_forklift_result")), None, rules=RULES)
    assert any(w in res.label for w in ("亡", "死", "撞")), res.label


# --- missing place is a hint, not an error ------------------------------------------------

def test_missing_place_is_not_a_validation_error():
    src = labeler.LabelSource(["新北土城一七旬老翁 轉彎不慎遭貨車輾亡"], "")
    assert "LBL-4" not in labeler.validate("老翁遭輾亡", src, RULES)


def test_non_social_story_gets_no_place_warning():
    src = source_of(load_case("tvbs_1251_france_open"))
    assert labeler.REASON_NO_PLACE not in labeler.review_reasons("法國動亂", src)


def test_social_story_without_place_is_info_only():
    src = labeler.LabelSource(["新北土城一七旬老翁 轉彎不慎遭貨車輾亡"], "")
    notes = labeler.review_notes("老翁遭輾亡", src)
    assert notes and all(n["level"] == labeler.LEVEL_INFO for n in notes if "地點" in n["reason"])


# --- model JSON answer and evidence check ---------------------------------------------------

def test_model_json_evidence_must_be_in_source():
    case = load_case("cts_1200_forklift_result")
    good = json.dumps({"short_title": "堆高機撞婦亡", "evidence": ["堆高機卸貨沒放警示害婦撞死"], "issues": []},
                      ensure_ascii=False)
    fake = json.dumps({"short_title": "堆高機撞婦亡", "evidence": ["駕駛酒駕撞死婦人"], "issues": []}, ensure_ascii=False)
    assert labeler.make_label(source_of(case), Scripted(good), rules=RULES).label == "堆高機撞婦亡"
    res = labeler.make_label(source_of(case), Scripted(fake), rules=RULES)
    assert res.history and "EVD-SRC" in res.history[0]


def test_model_issues_become_review_reason():
    case = load_case("cts_1206_name_unstable")
    ans = json.dumps({"short_title": "張俊傑偷渡", "evidence": ["張俊傑偷渡逃亡"], "issues": ["「詭助」無法辨認"]},
                     ensure_ascii=False)
    res = labeler.make_label(source_of(case), Scripted(ans), rules=RULES)
    assert res.label == "張俊傑偷渡"
    assert any("無法辨認" in r for r in res.reasons)


def test_prompt_version_bumped_and_in_cache_key(tmp_path):
    assert llm.PROMPT_VERSION not in ("v7-trad-words",)
    cg = llm.CachedGenerator(llm.OllamaGenerator(), tmp_path / "c.json")
    assert llm.PROMPT_VERSION in cg.key(labeler.LabelSource(["x"]), 0)
    assert "short_title" in llm.SYSTEM and "evidence" in llm.SYSTEM


# --- negation kept (CTS 12:32 「所幸無造成人員傷亡」) -----------------------------------------------

def test_negated_casualty_cannot_be_stated():
    src = labeler.LabelSource(["警消歷經7小時撲滅火勢所幸無造成人員傷亡"])
    assert "NEG-DROP" in labeler.validate("造成人員傷亡", src, RULES)
    assert "EVT-MISS" not in labeler.validate("撲滅火勢", src, RULES)
    assert "傷亡" not in labeler.make_label(src, None, rules=RULES).label


def test_real_casualty_still_required():
    src = labeler.LabelSource(["臨停卸貨未警示.也未申請臨時證違規害婦撞亡"])
    assert "EVT-MISS" in labeler.validate("臨停卸貨", src, RULES)
