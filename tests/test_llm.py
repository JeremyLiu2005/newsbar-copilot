"""LLM generators with fake transports — no network, no keys."""

import io
import json
from types import SimpleNamespace

import pytest

from newsbar.labeler import LabelSource, make_label
from newsbar.llm import (MAX_ASR_CHARS, ClaudeGenerator, ExternalCallNotAllowed, OllamaGenerator,
                         build_user_prompt, clean_output, from_rules)

SRC = LabelSource(["新北土城一七旬老翁 轉彎不慎遭貨車輾亡"], "土城" * 300)


def test_clean_output():
    assert clean_output("五字標：「土城翁輾亡」。\n說明…") == "土城翁輾亡"


def test_prompt_caps_speech_excerpt():
    p = build_user_prompt(SRC, 0)
    assert p.count("土城") <= MAX_ASR_CHARS // 2 + 2
    assert "沒有通過檢查" in build_user_prompt(LabelSource(SRC.bar_texts, SRC.asr_text, "「土城」不在主標題裡"), 1)


def test_ollama_generator_round_trip():
    seen = {}

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def opener(req, timeout):
        seen["body"] = json.loads(req.data)
        return Resp(json.dumps({"message": {"content": "土城翁輾亡"}}).encode())

    gen = OllamaGenerator()
    gen.opener = opener
    res = make_label(SRC, gen)
    assert res.label == "土城翁輾亡" and res.fallback is None
    assert seen["body"]["messages"][0]["role"] == "system"
    assert seen["body"]["stream"] is False


def test_claude_refused_without_opt_in():
    with pytest.raises(ExternalCallNotAllowed):
        ClaudeGenerator()


class FakeClaude:
    def __init__(self, text, stop="end_turn"):
        self.calls = []
        msgs = SimpleNamespace(create=self._create)
        self.beta = SimpleNamespace(messages=msgs)
        self.text, self.stop = text, stop

    def _create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(stop_reason=self.stop, content=[SimpleNamespace(type="text", text=self.text)])


def test_claude_generator_sends_only_text_and_uses_fallbacks():
    fake = FakeClaude("土城翁輾亡")
    gen = ClaudeGenerator(allow_external=True, client=fake)
    assert make_label(SRC, gen).label == "土城翁輾亡"
    kw = fake.calls[0]
    assert kw["model"] == "claude-opus-5-5"
    assert kw["fallbacks"] == "default" and kw["betas"] == ["server-side-fallback-2026-07-01"]
    assert all(isinstance(m["content"], str) for m in kw["messages"])  # no images/video


def test_claude_refusal_goes_to_rule_fallback():
    gen = ClaudeGenerator(allow_external=True, client=FakeClaude("", stop="refusal"))
    res = make_label(SRC, gen)
    assert res.fallback == "FB-4" and "土城" in res.label and "輾亡" in res.label


def test_from_rules():
    assert from_rules({}) is None
    assert isinstance(from_rules({"labeler": {"generator": "ollama"}}), OllamaGenerator)
    with pytest.raises(ExternalCallNotAllowed):
        from_rules({"labeler": {"generator": "claude"}})


def test_ollama_request_is_deterministic():
    import json
    from newsbar.labeler import LabelSource
    from newsbar.llm import OllamaGenerator

    sent = {}

    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self): return json.dumps({"message": {"content": "土城翁輾亡"}}).encode()

    def opener(req, timeout):
        sent.update(json.loads(req.data))
        return Resp()

    g = OllamaGenerator()
    g.opener = opener
    g.generate(LabelSource(["新北土城一七旬老翁 轉彎不慎遭貨車輾亡"]), 0)
    assert sent["options"]["temperature"] == 0 and "seed" in sent["options"]


def test_cached_generator_replays_answers(tmp_path):
    from newsbar.labeler import LabelSource
    from newsbar.llm import CachedGenerator

    class Count:
        n = 0
        def generate(self, source, attempt):
            Count.n += 1
            return f"答{Count.n}"

    src = LabelSource(["新北土城一七旬老翁 轉彎不慎遭貨車輾亡"])
    first = CachedGenerator(Count(), tmp_path / "c.json").generate(src, 0)
    again = CachedGenerator(Count(), tmp_path / "c.json").generate(src, 0)
    assert first == again == "答1" and Count.n == 1
