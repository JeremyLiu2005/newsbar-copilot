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

SYSTEM = """你是台灣電視新聞監看員，要替一則新聞寫「新聞短標」（俗稱五字標），讓看監看表的人一眼認得是哪則新聞。
請依下列順序思考，最後只輸出一個 JSON 物件，不要其他文字：
{"short_title": "短標", "evidence": ["短標依據的來源原文片段"], "issues": ["來源互相衝突或無法辨認之處；沒有就空陣列"]}
規則：
1. 優先保留主要行為與結果（例：婦人被堆高機撞死 → 要有「撞」或「亡」，不能只寫「堆高機警示」）。
2. 不得新增來源沒有的人名、地點、數字、傷亡或因果；只能用主標題裡出現過的字，照繁體原字，不可寫成簡體字。
3. 約 5 個字為目標，測試版允許 4–6 個字（英文專有名詞、每個數字各算一個字）。
4. 不得直接截取標題前幾個字；詞不可從中間切斷（「一度」不能只寫「一」）；數字和單位要一起用或都不用（「6人」不能只寫「6」）；句子要說完（不能停在「陷入」「再」這類後面還要接東西的字）。
5. 來源沒有地點就不要補地點；有地點但不影響辨認時可以省略。
6. 來源只說「起訴」「求刑」「疑似」時，不得寫成「判刑」「確定」等定論；來源有「疑似」「傳出」「涉嫌」「據傳」這類未確認說法，短標要保留。
7. evidence 必須逐字引用主標題（或沒有標題時的語音）裡的片段，不可改寫。
8. 標題字看起來是錯字、人名無法辨認或來源互相衝突時，寫進 issues，不要猜。"""

FEW_SHOT = [
    ("新北土城一七旬老翁 轉彎不慎遭貨車輾亡",
     '{"short_title": "土城翁輾亡", "evidence": ["新北土城一七旬老翁", "遭貨車輾亡"], "issues": []}'),
    ("張俊傑「9分鐘偷渡術」曝 高金素梅共諜案起訴求刑14年",
     '{"short_title": "高金求刑14年", "evidence": ["高金素梅共諜案起訴求刑14年"], "issues": []}'),
    ("過彎不慎墜橋 駕駛送醫不治",
     '{"short_title": "過彎墜橋亡", "evidence": ["過彎不慎墜橋", "送醫不治"], "issues": []}'),
    ("法國再陷入動亂各城罷工罷課巴黎鐵塔又關",
     '{"short_title": "法國陷動亂", "evidence": ["法國再陷入動亂"], "issues": []}'),
    ("台中潭子男子酒駕撞護欄 闖紅燈遭攔查",
     '{"short_title": "潭子酒駕撞", "evidence": ["台中潭子男子酒駕撞護欄"], "issues": []}'),
]
PROMPT_VERSION = "v8-event-json"  # bump when SYSTEM or FEW_SHOT changes so cached answers are not replayed
PROMPT_VERSION_RAG = "v5-rag-json"  # used only while retrieval is on

# 規則 NFR-14：外部 AI 最多只送 200 字語音節錄
MAX_ASR_CHARS = 200  # NFR-14: never send more speech than needed


def build_user_prompt(source: LabelSource, attempt: int) -> str:
    # The label must be built from the on-screen title; speech is offered only when no
    # title was read (FB-3). With speech in the prompt the model borrowed places and
    # verbs that are not in the title and failed validation (11/06 run: 49/91 cells).
    # 規則 SRC-2：標籤從主標題來；只有讀不到標題時才把語音給 AI（規則 FB-3）
    bar = " / ".join(source.bar_texts[:2]) or "（主標題讀不到）"
    asr = "" if source.bar_texts else (source.asr_text or "")[:MAX_ASR_CHARS]
    retry = ""
    if source.feedback:  # 規則 §6.3：帶具體原因重寫
        retry = f"\n上一次的答案沒有通過檢查：{source.feedback}。請依規則重寫，只輸出 JSON。"
    return f"主標題：{bar}\n主播與記者語音節錄：{asr or '（無）'}{retry}\n請輸出 JSON："


def _turn(q: str, a: str) -> list[dict]:
    return [{"role": "user", "content": f"主標題：{q}\n主播與記者語音節錄：（無）\n請輸出 JSON："},
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
    """The model's JSON object as text (labeler.parse_answer reads it); a plain one-line answer is
    still cleaned the old way."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if m:
        return m.group()
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

    def __init__(self, inner, max_failures: int = 3, cooldown_sec: float = 30.0, clock=time.monotonic):
        self.inner, self.max_failures, self.cooldown_sec, self.clock = inner, max_failures, cooldown_sec, clock
        self.model = getattr(inner, "model", "")
        self._fails = 0
        self._tripped_at: float | None = None
        self._lock = threading.Lock()

    def rag_context(self, source: LabelSource):
        ctx = getattr(self.inner, "rag_context", None)
        return ctx(source) if ctx else None

    @property
    def tripped(self) -> bool:
        return self._fails >= self.max_failures

    def generate(self, source: LabelSource, attempt: int) -> str:
        # 規則 R20-2：連續失敗 3 次後，不再等待逾時，直接丟錯讓標籤改用規則備援；
        # 但每隔 30 秒放一個請求去試（服務恢復或模型載入完成就自動接回），不會整次執行都放棄
        with self._lock:
            if self.tripped:
                now = self.clock()
                if self._tripped_at is not None and now - self._tripped_at < self.cooldown_sec:
                    raise LLMError("AI 服務連續無回應，稍後再試")
                self._tripped_at = now  # this call is the probe; others keep failing fast meanwhile
        try:
            out = self.inner.generate(source, attempt)
        except Exception:
            with self._lock:
                self._fails += 1
                if self.tripped and self._tripped_at is None:
                    self._tripped_at = self.clock()
            raise
        with self._lock:
            self._fails = 0
            self._tripped_at = None
        return out


class UnavailableGenerator:
    """Stand-in when the service could not be reached before labeling: every cell takes the
    rule-label path and carries the 「AI 服務無回應」 reason."""

    def generate(self, source: LabelSource, attempt: int) -> str:
        raise LLMError("Ollama 未啟動")


def warm_up(gen, timeout: float = 300.0, say=print) -> bool:
    """Load the model before labeling (R20-2). A cold Ollama needs tens of seconds to load a 7B
    model; with four channels asking at once the first requests time out and used to trip the
    breaker for a whole channel (11/22 rerun: TVBS 21 cells 「AI 服務無回應」). One tiny request
    with a long timeout loads it; False = the service answers /api/tags but cannot generate."""
    host, model = getattr(gen, "host", None), getattr(gen, "model", None)
    if not host or not model:
        return True
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": "好"}], "stream": False,
                       "options": {"num_predict": 1}}).encode()
    req = urllib.request.Request(f"{host}/api/chat", data=body, headers={"Content-Type": "application/json"})
    t0 = time.monotonic()
    try:
        with getattr(gen, "opener", urllib.request.urlopen)(req, timeout=timeout) as r:
            json.loads(r.read().decode())
    except (OSError, http.client.HTTPException, ValueError) as e:
        say(f"⚠ 模型 {model} 無法載入（{type(e).__name__}: {e}）；五字標改用規則擷取，並標為低信心")
        return False
    if time.monotonic() - t0 > 5:
        say(f"模型 {model} 已載入（{time.monotonic() - t0:.0f} 秒）")
    return True


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
