"""LLM label generators (spec §6; NFR-07, NFR-14 data scope).

Every generator only proposes; labeler.validate() decides. Two backends:
  * OllamaGenerator  — local model over Ollama's HTTP API (default, data stays in-house)
  * ClaudeGenerator  — Anthropic API via the official SDK; refused unless the
    rules explicitly allow external calls, and it only ever sends the bar text
    and a short speech excerpt (no video, no frames).
"""

from __future__ import annotations

import http.client
import json
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from .labeler import LabelSource

SYSTEM = """你是台灣電視新聞監看員，要替一則新聞寫「五字標」，讓看監看表的人認得是哪則新聞。
規則：
1. 剛好 5 個字，不能多也不能少（英文專有名詞算一個字），只輸出五字標本身，不要解釋、不要標點、不要引號。
2. 只能用主標題裡出現過的字（人名、地名、事物、動作、數字）；不得補造，也不要用標題以外的地名。
3. 社會事件（車禍、命案、竊盜、火警等）一定要含地點，地點必須出現在來源中，優先用較小的地名（例：新北土城→土城）。
4. 來源只說「起訴」「求刑」「疑似」時，不得寫成「判刑」「確定」等定論；來源有「疑」「疑似」「傳出」「恐」「涉嫌」「涉」「據傳」「爆料」這類未確認的說法，五字標要保留其中一個字（例：疑縱火、傳出離婚），不能寫成肯定句。
5. 人名可以只取名字（張俊傑→俊傑），機構可用常見簡稱。
6. 先數字數：超過 5 個字就刪修飾語（時間、程度、「不慎」「怒批」等），只留「地點＋核心動作＋結果」；少於 5 個字就補上地點或結果。"""

FEW_SHOT = [
    ("新北土城一七旬老翁 轉彎不慎遭貨車輾亡", "土城翁輾亡"),
    ("張俊傑「9分鐘偷渡術」曝 高金素梅共諜案起訴求刑14年", "高金遭起訴"),
    ("過彎不慎墜橋 駕駛送醫不治", "過彎墜橋亡"),
    ("萬聖節派對後垃圾亂丟 民眾怒批", "萬聖節亂丟"),
    ("台中潭子男子酒駕撞護欄 闖紅燈遭攔查", "潭子酒駕撞"),
]
PROMPT_VERSION = "v6-exact5-fb"  # bump when SYSTEM or FEW_SHOT changes so cached answers are not replayed
PROMPT_VERSION_RAG = "v4-rag"  # used only while retrieval is on; with it off the v3 prompt and cache keys are unchanged

# 規則 NFR-14：外部 AI 最多只送 200 字語音節錄
MAX_ASR_CHARS = 200  # NFR-14: never send more speech than needed


def build_user_prompt(source: LabelSource, attempt: int) -> str:
    # The label must be built from the on-screen title; speech is offered only when no
    # title was read (FB-3). With speech in the prompt the model borrowed places and
    # verbs that are not in the title and failed validation (11/06 run: 49/91 cells).
    # 規則 SRC-2：標籤從主標題來；只有讀不到標題時才把語音給 AI（規則 FB-3）
    bar = " / ".join(source.bar_texts[:2]) or "（主標題讀不到）"
    asr = "" if source.bar_texts else (source.asr_text or "")[:MAX_ASR_CHARS]
    retry = "\n上一次的答案沒有通過檢查（可能用了來源沒有的字、缺地點，或把「疑似／傳出／恐／涉嫌」這類未確認的說法拿掉了），請只用來源裡的字重寫。" if attempt else ""
    if source.feedback:  # 規則 §6.3：回饋重試，明講上一個答案錯在哪
        retry = f"\n上一次的答案沒有通過檢查：{source.feedback}。請只用主標題裡的字，重寫剛好 5 個字。"
    return f"主標題：{bar}\n主播與記者語音節錄：{asr or '（無）'}{retry}\n五字標："


def _turn(q: str, a: str) -> list[dict]:
    return [{"role": "user", "content": f"主標題：{q}\n主播與記者語音節錄：（無）\n五字標："},
            {"role": "assistant", "content": a}]


def retrieved_pairs(rag, source: LabelSource, exclude_ids=(), exclude_labels=()) -> list:
    """KB records most similar to the title on screen (none when RAG is off or no title was read).
    Pairs the prompt already carries as FEW_SHOT are not repeated."""
    # 規則 RAG-1：預設關閉（rag 為空）；打開時才檢索相似的人工確認標籤當範例
    if rag is None or not source.bar_texts:
        return []
    return rag.retrieve(" / ".join(source.bar_texts[:2]), exclude_ids, exclude_labels, set(FEW_SHOT))


def build_messages(source: LabelSource, attempt: int, extra: list | None = None) -> list[dict]:
    """Few-shot turns (FEW_SHOT, then any retrieved pairs) followed by the question. Without
    `extra` this is exactly the v3 message list."""
    msgs: list[dict] = []
    for q, a in FEW_SHOT:
        msgs += _turn(q, a)
    for r in extra or []:
        msgs += _turn(r.title, r.label)
    msgs.append({"role": "user", "content": build_user_prompt(source, attempt)})
    return msgs


def clean_output(text: str) -> str:
    line = text.strip().splitlines()[0] if text.strip() else ""
    line = re.sub(r"^(五字標|答案)[:：]\s*", "", line)
    return line.strip(" 「」『』\"'。．.")


class LLMError(RuntimeError):
    """The model service did not answer usefully (down, timed out, HTTP error, malformed reply)."""


@dataclass
class OllamaGenerator:
    model: str = "qwen2.5:7b-instruct"
    host: str = "http://127.0.0.1:11434"
    timeout: float = 60.0
    rag: object = None  # kb.Rag; None = retrieval off
    rag_exclude_ids: tuple = ()
    rag_exclude_labels: tuple = ()
    opener = staticmethod(urllib.request.urlopen)

    def rag_pairs(self, source: LabelSource) -> list:
        return retrieved_pairs(self.rag, source, self.rag_exclude_ids, self.rag_exclude_labels)

    def rag_context(self, source: LabelSource) -> list[str] | None:
        """Ids (and labels, so a refreshed label changes the key) of the pairs this prompt will carry; None while retrieval is off (cache key stays v3)."""
        return None if self.rag is None else [f"{r.id}={r.label}" for r in self.rag_pairs(source)]

    def generate(self, source: LabelSource, attempt: int) -> str:
        msgs = [{"role": "system", "content": SYSTEM}] + build_messages(source, attempt, self.rag_pairs(source))
        body = json.dumps({"model": self.model, "messages": msgs, "stream": False,
                           "options": {"temperature": 0, "seed": 42}}).encode()  # same video, same sheet
        req = urllib.request.Request(f"{self.host}/api/chat", data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            with self.opener(req, timeout=self.timeout) as r:
                data = json.loads(r.read().decode())
            return clean_output(data["message"]["content"])
        except (OSError, http.client.HTTPException, ValueError, KeyError, TypeError, AttributeError) as e:
            # URLError/timeout/ConnectionError are OSError; JSONDecodeError is ValueError
            raise LLMError(f"{type(e).__name__}: {e}") from e


class ExternalCallNotAllowed(RuntimeError):
    pass


class ClaudeGenerator:
    """Anthropic Messages API through the official `anthropic` SDK (`uv add anthropic`)."""

    def __init__(self, model: str = "claude-opus-5-5", allow_external: bool = False, client=None, rag=None):
        # 規則 NFR-14：外部 AI 預設不准用，要在規則檔明確打開
        if not allow_external:
            raise ExternalCallNotAllowed(
                "外部 LLM 預設關閉（NFR-14）。要使用請在 rules.yaml 設 labeler.allow_external: true")
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        self.client = client
        self.model = model
        self.rag = rag

    def rag_pairs(self, source: LabelSource) -> list:
        return retrieved_pairs(self.rag, source)

    def rag_context(self, source: LabelSource) -> list[str] | None:
        return None if self.rag is None else [f"{r.id}={r.label}" for r in self.rag_pairs(source)]

    def generate(self, source: LabelSource, attempt: int) -> str:
        msgs = build_messages(source, attempt, self.rag_pairs(source))
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


class GuardedGenerator:
    """Stops asking a dead service. After `max_failures` consecutive errors every further call
    raises at once instead of waiting for its own timeout, so ~100 cells do not cost ~100 minutes
    of 60 s timeouts. One success resets the count. The labeler catches the error per cell."""

    def __init__(self, inner, max_failures: int = 3):
        self.inner, self.max_failures = inner, max_failures
        self.model = getattr(inner, "model", "")
        self._fails = 0
        self._lock = threading.Lock()

    def rag_context(self, source: LabelSource):
        ctx = getattr(self.inner, "rag_context", None)
        return ctx(source) if ctx else None

    @property
    def tripped(self) -> bool:
        return self._fails >= self.max_failures

    def generate(self, source: LabelSource, attempt: int) -> str:
        # 規則 R20-2：連續失敗 3 次後，不再等待逾時，直接丟錯讓標籤改用規則備援
        if self.tripped:
            raise LLMError("AI 服務連續無回應，本次不再重試")
        try:
            out = self.inner.generate(source, attempt)
        except Exception:
            with self._lock:
                self._fails += 1
            raise
        with self._lock:
            self._fails = 0
        return out


class UnavailableGenerator:
    """Stand-in when the service could not be reached before labeling: every cell takes the
    rule-label path and carries the 「AI 服務無回應」 reason."""

    def generate(self, source: LabelSource, attempt: int) -> str:
        raise LLMError("Ollama 未啟動")


def ollama_up(host: str, timeout: float = 2.0, opener=urllib.request.urlopen) -> bool:
    try:
        with opener(f"{host.rstrip('/')}/api/tags", timeout=timeout) as r:
            return 200 <= getattr(r, "status", 200) < 300
    except (OSError, http.client.HTTPException, ValueError):
        return False


def ensure_ollama(host: str, *, ping=ollama_up, which=shutil.which, spawn=subprocess.Popen,
                  wait_sec: float = 15.0, sleep=time.sleep, say=print) -> bool:
    """True when Ollama answers at `host`. If it does not, start `ollama serve` once in the
    background and wait briefly; False (with a clear message) when it still is not there."""
    if ping(host):
        return True
    exe = which("ollama")
    if not exe:
        say(f"⚠ Ollama 沒有回應（{host}），也找不到 ollama 指令；五字標改用規則擷取，並標為低信心")
        return False
    say(f"Ollama 沒有回應（{host}），嘗試啟動 ollama serve …")
    try:
        spawn([exe, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
              start_new_session=True)
    except OSError as e:
        say(f"⚠ 無法啟動 ollama serve（{e}）；五字標改用規則擷取，並標為低信心")
        return False
    waited = 0.0
    while waited < wait_sec:
        sleep(0.5)
        waited += 0.5
        if ping(host):
            say("Ollama 已啟動")
            return True
    say(f"⚠ 等了 {wait_sec:.0f} 秒 Ollama 仍沒有回應；五字標改用規則擷取，並標為低信心")
    return False


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

    def key(self, source: LabelSource, attempt: int) -> str:
        model, prompt = getattr(self.inner, "model", ""), build_user_prompt(source, attempt)
        ctx = getattr(self.inner, "rag_context", None)
        ids = ctx(source) if ctx else None
        if ids is None:  # retrieval off: the same key as before RAG existed
            return f"{model}|{PROMPT_VERSION}|{attempt}|{prompt}"
        return f"{model}|{PROMPT_VERSION_RAG}|{attempt}|{','.join(ids)}|{prompt}"

    def generate(self, source: LabelSource, attempt: int) -> str:
        key = self.key(source, attempt)
        with self._lock:
            if key in self._cache:
                return self._cache[key]
        out = self.inner.generate(source, attempt)
        with self._lock:
            self._cache[key] = out
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self._cache, ensure_ascii=False, indent=0))
        return out


def from_rules(rules: dict | None, kb_override: str | None = None):
    """labeler.generator: rule | ollama | claude  (default rule = no LLM)."""
    cfg = (rules or {}).get("labeler", {}) or {}
    kind = cfg.get("generator", "rule")
    if kind == "rule":
        return None
    from . import kb

    rag = kb.rag_from_rules(rules, kb_override)  # None unless labeler.rag.enabled (or --kb)
    if kind == "ollama":
        return OllamaGenerator(model=cfg.get("model", OllamaGenerator.model), host=cfg.get("host", OllamaGenerator.host),
                               rag=rag)
    if kind == "claude":
        return ClaudeGenerator(model=cfg.get("model", "claude-opus-5-5"), allow_external=bool(cfg.get("allow_external")),
                               rag=rag)
    raise ValueError(f"unknown labeler.generator {kind!r}")
