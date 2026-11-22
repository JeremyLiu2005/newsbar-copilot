import json
from pathlib import Path

import pytest

from newsbar import labeler, lexicon

CASES = Path(__file__).parent / "cases"
ROOT = Path(__file__).resolve().parents[2]


def load_case(case_id: str) -> dict:
    return json.loads((CASES / f"{case_id}.json").read_text(encoding="utf-8"))


def source_of(case: dict) -> labeler.LabelSource:
    s = case["source"]
    return labeler.LabelSource(list(s["bar_texts"]), s["asr_text"] or "")


class Scripted:
    """Fixed model replies (monkeypatched model): returns the next scripted answer per call."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def generate(self, source, attempt):
        self.calls.append((attempt, source.feedback))
        if not self.answers:
            raise RuntimeError("no more scripted answers")
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


@pytest.fixture(autouse=True)
def _project_lexicon():
    prev = lexicon._active
    lexicon.use(str(ROOT / "config" / "lexicon.yaml"))
    yield
    lexicon._active = prev
