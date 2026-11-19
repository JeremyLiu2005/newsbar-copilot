"""LLM label generators (spec §6; NFR-07, NFR-14 data scope).

Every generator only proposes; labeler.validate() decides. Two backends:
  * OllamaGenerator  — local model over Ollama's HTTP API (default, data stays in-house)
  * ClaudeGenerator  — Anthropic API via the official SDK; refused unless the
    rules explicitly allow external calls, and it only ever sends the bar text
    and a short speech excerpt (no video, no frames).
"""

from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass

from .labeler import LabelSource

SYSTEM = """你是台灣電視新聞監看員，要替一則新聞寫「五字標」，讓看監看表的人認得是哪則新聞。
規則：
1. 4–6 個字（英文專有名詞算一個字），只輸出五字標本身，不要解釋、不要標點、不要引號。
2. 只能用主標題裡出現過的字（人名、地名、事物、動作、數字）；不得補造，也不要用標題以外的地名。
3. 社會事件（車禍、命案、竊盜、火警等）一定要含地點，地點必須出現在來源中，優先用較小的地名（例：新北土城→土城）。
4. 來源只說「起訴」「求刑」「疑似」時，不得寫成「判刑」「確定」等定論。
5. 人名可以只取名字（張俊傑→俊傑），機構可用常見簡稱。"""

FEW_SHOT = [
    ("新北土城一七旬老翁 轉彎不慎遭貨車輾亡", "土城翁輾亡"),
    ("張俊傑「9分鐘偷渡術」曝 高金素梅共諜案起訴求刑14年", "高金遭起訴"),
    ("過彎不慎墜橋 駕駛送醫不治", "過彎墜橋亡"),
    ("萬聖節派對後垃圾亂丟 民眾怒批", "萬聖節亂丟"),
]

MAX_ASR_CHARS = 200  # NFR-14: never send more speech than needed


def build_user_prompt(source: LabelSource, attempt: int) -> str:
    # The label must be built from the on-screen title; speech is offered only when no
    # title was read (FB-3). With speech in the prompt the model borrowed places and
    # verbs that are not in the title and failed validation (11/06 run: 49/91 cells).
    bar = " / ".join(source.bar_texts[:2]) or "（主標題讀不到）"
    asr = "" if source.bar_texts else (source.asr_text or "")[:MAX_ASR_CHARS]
    retry = "\n上一次的答案沒有通過檢查（可能用了來源沒有的字，或缺地點），請只用來源裡的字重寫。" if attempt else ""
    return f"主標題：{bar}\n主播與記者語音節錄：{asr or '（無）'}{retry}\n五字標："


def clean_output(text: str) -> str:
    line = text.strip().splitlines()[0] if text.strip() else ""
    line = re.sub(r"^(五字標|答案)[:：]\s*", "", line)
    return line.strip(" 「」『』\"'。．.")


@dataclass
class OllamaGenerator:
    model: str = "qwen2.5:7b-instruct"
    host: str = "http://127.0.0.1:11434"
    timeout: float = 60.0
    opener = staticmethod(urllib.request.urlopen)

    def generate(self, source: LabelSource, attempt: int) -> str:
        msgs = [{"role": "system", "content": SYSTEM}]
        for q, a in FEW_SHOT:
            msgs += [{"role": "user", "content": f"主標題：{q}\n主播與記者語音節錄：（無）\n五字標："},
                     {"role": "assistant", "content": a}]
        msgs.append({"role": "user", "content": build_user_prompt(source, attempt)})
        body = json.dumps({"model": self.model, "messages": msgs, "stream": False,
                           "options": {"temperature": 0, "seed": 42}}).encode()  # same video, same sheet
        req = urllib.request.Request(f"{self.host}/api/chat", data=body,
                                     headers={"Content-Type": "application/json"})
        with self.opener(req, timeout=self.timeout) as r:
            data = json.loads(r.read().decode())
        return clean_output(data["message"]["content"])


class ExternalCallNotAllowed(RuntimeError):
    pass


class ClaudeGenerator:
    """Anthropic Messages API through the official `anthropic` SDK (`uv add anthropic`)."""

    def __init__(self, model: str = "claude-opus-5-5", allow_external: bool = False, client=None):
        if not allow_external:
            raise ExternalCallNotAllowed(
                "外部 LLM 預設關閉（NFR-14）。要使用請在 rules.yaml 設 labeler.allow_external: true")
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        self.client = client
        self.model = model

    def generate(self, source: LabelSource, attempt: int) -> str:
        msgs = []
        for q, a in FEW_SHOT:
            msgs += [{"role": "user", "content": f"主標題：{q}\n主播與記者語音節錄：（無）\n五字標："},
                     {"role": "assistant", "content": a}]
        msgs.append({"role": "user", "content": build_user_prompt(source, attempt)})
        resp = self.client.beta.messages.create(
            model=self.model,
            max_tokens=4000,
            system=SYSTEM,
            messages=msgs,
            output_config={"effort": "low"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if resp.stop_reason == "refusal":
            return ""  # validator rejects → retry → rule fallback (FB-4)
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        return clean_output(text)


class CachedGenerator:
    """Remember each answer by its exact prompt so the same video gives the same sheet.

    temperature 0 alone is not enough: Ollama batches parallel requests and one label in
    240 still changed between identical runs (11/06). The cache also skips repeat calls.
    """

    def __init__(self, inner, path):
        import threading
        from pathlib import Path

        self.inner, self.path = inner, Path(path)
        self._lock = threading.Lock()
        self._cache: dict[str, str] = json.loads(self.path.read_text()) if self.path.exists() else {}

    def generate(self, source: LabelSource, attempt: int) -> str:
        key = f"{getattr(self.inner, 'model', '')}|{attempt}|{build_user_prompt(source, attempt)}"
        with self._lock:
            if key in self._cache:
                return self._cache[key]
        out = self.inner.generate(source, attempt)
        with self._lock:
            self._cache[key] = out
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self._cache, ensure_ascii=False, indent=0))
        return out


def from_rules(rules: dict | None):
    """labeler.generator: rule | ollama | claude  (default rule = no LLM)."""
    cfg = (rules or {}).get("labeler", {}) or {}
    kind = cfg.get("generator", "rule")
    if kind == "rule":
        return None
    if kind == "ollama":
        return OllamaGenerator(model=cfg.get("model", OllamaGenerator.model), host=cfg.get("host", OllamaGenerator.host))
    if kind == "claude":
        return ClaudeGenerator(model=cfg.get("model", "claude-opus-5-5"), allow_external=bool(cfg.get("allow_external")))
    raise ValueError(f"unknown labeler.generator {kind!r}")
