"""rulebook.py 寫的數字必須等於程式實際的預設值，文件才不會悄悄與程式脫節。"""

import json
import re
from pathlib import Path

import pytest

from newsbar import cli, lexicon, rulebook

ROOT = Path(__file__).resolve().parents[1]
GROUPS = ["noise", "ad", "change", "minute", "label", "fallback", "optional"]
ALL_PARAMS = [(r.id, p) for r in rulebook.RULES for p in r.params]


def test_every_group_has_rules_and_ids_are_unique():
    ids = [r.id for r in rulebook.RULES]
    assert len(ids) == len(set(ids))
    assert [k for k, *_ in rulebook.GROUPS] == GROUPS
    for key in GROUPS:
        assert any(r.group == key for r in rulebook.RULES), key


@pytest.mark.parametrize("rule_id,p", ALL_PARAMS, ids=[f"{i}:{p.name}" for i, p in ALL_PARAMS])
def test_stated_value_equals_the_value_the_code_uses(rule_id, p):
    actual = p.actual()
    if p.unit == "字集":  # 字的集合，與順序無關
        assert set(actual) == set(p.value) and len(actual) == len(set(actual)), (rule_id, p.name)
        return
    assert actual == p.value and type(actual) is type(p.value), (rule_id, p.name, p.value, actual)


@pytest.mark.parametrize("rule", rulebook.RULES, ids=lambda r: r.id)
def test_every_code_location_exists(rule):
    assert rule.where
    for w in rule.where:
        assert rulebook.resolve(w) is not None, w


def test_the_key_defaults_are_all_covered():
    """The thresholds the client's pain points hang on must be stated, not just exist in code."""
    names = {p.name for _, p in ALL_PARAMS}
    for must in ["min_signals", "min_duration_sec", "return_confirm_sec", "return_timeout_sec", "outage_black_sec",
                 "merge_gap_sec", "min_break_sec", "stable_sec", "asr_window_sec", "visual_window_sec", "credit_max_sec",
                 "credit_window_sec", "recur_window_sec", "min_story_before_ad_sec", "min_len", "max_len",
                 "VoteParams.window_sec", "PaddleBackend.min_conf", "unify(window_sec=)", "carousel_min_titles"]:
        assert must in names, must


def test_every_dataclass_threshold_of_the_ad_and_segmenter_params_is_listed():
    from dataclasses import fields

    from newsbar.ad_state import AdParams
    from newsbar.segmenter import SegParams

    listed = {p.name for _, p in ALL_PARAMS}
    skip = {"model", "mode", "min_conf"}  # model path; mode listed as AdParams.mode/SegParams.mode; un-voted OCR input
    for cls in (AdParams, SegParams):
        for f in fields(cls):
            if f.name not in skip:
                assert f.name in listed, f"{cls.__name__}.{f.name} 沒有寫進規則總表"


def test_lexicon_words_named_in_the_text_exist():
    lex = lexicon.get()
    for w in ["車禍", "撞", "輾", "火警", "爆炸", "槍", "毒駕", "詐騙", "殺人", "墜樓", "聲押"]:
        assert w in lex.social_keywords, w
    for single in ["搶", "偷", "死", "傷", "殺"]:  # the text says these ambiguous single characters are not keywords
        assert single not in lex.social_keywords


def test_code_comments_cite_known_rule_ids():
    ids = {r.id for r in rulebook.RULES}
    seen = set()
    for f in (ROOT / "src/newsbar").glob("*.py"):
        if f.name == "rulebook.py":
            continue
        for m in re.finditer(r"# 規則 ([^：:\n（]+)", f.read_text(encoding="utf-8")):
            for rid in re.split(r"[、，,～]", m.group(1)):
                rid = rid.strip()
                if rid and re.match(r"^[A-Za-z§]", rid):
                    seen.add(rid)
    unknown = {s for s in seen if s not in ids and not re.match(r"^(AD|NEW|SAME|MAP|OUT)-[*]$", s)}
    assert not unknown, f"程式註解引用了規則總表沒有的編號：{sorted(unknown)}"
    for f in ("segmenter", "ad_state", "labeler", "minute_mapper", "llm", "pipeline"):
        assert "# 規則 " in (ROOT / f"src/newsbar/{f}.py").read_text(encoding="utf-8"), f


def test_docs_rules_md_is_up_to_date():
    assert (ROOT / "docs/rules.md").read_text(encoding="utf-8") == rulebook.render_markdown(), \
        "docs/rules.md 過期：uv run newsbar rules --markdown docs/rules.md"


def test_cli_prints_all_groups_and_writes_files(tmp_path, capsys):
    assert cli.main(["rules"]) == 0
    out = capsys.readouterr().out
    for title in (t for _, t, _ in rulebook.GROUPS):
        assert title in out
    md, js = tmp_path / "r.md", tmp_path / "r.json"
    assert cli.main(["rules", "--markdown", str(md), "--json", str(js)]) == 0
    assert md.read_text(encoding="utf-8") == rulebook.render_markdown()
    data = json.loads(js.read_text(encoding="utf-8"))
    assert [g["key"] for g in data["groups"]] == GROUPS
    rule = next(r for g in data["groups"] for r in g["rules"] if r["id"] == "AD-1")
    assert {"name": "min_duration_sec", "label": "連續成立的秒數", "value": 10, "unit": "秒"} in rule["params"]
