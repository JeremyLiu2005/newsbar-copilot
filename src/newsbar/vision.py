"""Optional cloud re-read of low-confidence title-bar crops (experimental, OFF by default).

Pattern borrowed from the Cmoney-Hackathon project: one adapter, a mode switch (off | mock | anthropic),
forced structured output, a timeout, and a guard that compares the model's text with what OCR read.

Rules that never change:
  * mode "off" is the default and `newsbar run` never calls this module;
  * only the cropped title bar (a small JPEG) is ever sent — no video, no audio, no full frame;
  * nothing is sent unless the caller passes --send; without it `vision-check` only counts what it would send;
  * the API key comes from the environment of whoever runs it (NEWSBAR_VISION_API_KEY or ANTHROPIC_API_KEY).
    A judge can plug in their own key; no key is shipped with the code and none is read from a file;
  * the model id is read from NEWSBAR_VISION_MODEL — it is not hard-coded, so it cannot go stale.

The result is a comparison file (OCR text vs model text per stable title), not a change to the sheet.
"""

from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from itertools import zip_longest
from pathlib import Path
from typing import Protocol

MIN_STABLE_SEC = 7  # same title this long = a stable title (matches the segmenter's 7 s rule)
LOW_CONF = 0.6  # Apple Vision confidence has three levels (0.3 / 0.5 / 1.0); <0.6 = not the top level
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
KEY_ENVS = ("NEWSBAR_VISION_API_KEY", "ANTHROPIC_API_KEY")


class VisionError(RuntimeError):
    """A single call failed (timeout, HTTP error, unusable answer). The caller records it and goes on."""


class VisionConfigError(ValueError):
    """The mode needs something the environment does not have (key, model id)."""


@dataclass
class VisionResult:
    text: str
    readable: bool = True
    note: str = ""


class VisionBackend(Protocol):
    def read_title(self, image: bytes, media_type: str, ocr_text: str) -> VisionResult: ...


class MockVision:
    """No cloud, no key: answers with the OCR text itself so the whole flow and the tests run offline."""

    def read_title(self, image: bytes, media_type: str, ocr_text: str) -> VisionResult:
        return VisionResult(text=ocr_text, readable=True, note="mock")


@dataclass
class AnthropicVision:
    """Anthropic Messages API over plain HTTPS (no SDK). Forced tool use keeps the answer structured."""

    api_key: str
    model: str
    timeout: float = 30.0
    url: str = ANTHROPIC_URL
    opener = staticmethod(urllib.request.urlopen)

    TOOL = {
        "name": "report_title",
        "description": "Report the headline text printed in the news title bar of this TV frame crop.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "The main headline exactly as printed. Empty if none."},
                "readable": {"type": "boolean", "description": "False if the crop has no readable headline."},
            },
            "required": ["title", "readable"],
        },
    }

    def read_title(self, image: bytes, media_type: str, ocr_text: str) -> VisionResult:
        body = json.dumps({
            "model": self.model,
            "max_tokens": 300,
            "tools": [self.TOOL],
            "tool_choice": {"type": "tool", "name": "report_title"},
            "messages": [{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type,
                                             "data": base64.b64encode(image).decode("ascii")}},
                {"type": "text", "text": "Copy the main headline from this Taiwanese TV news title bar. "
                                         "Do not add, translate or fix words. Ignore tickers and logos."},
            ]}],
        }).encode("utf-8")
        req = urllib.request.Request(self.url, data=body, method="POST", headers={
            "content-type": "application/json", "x-api-key": self.api_key, "anthropic-version": ANTHROPIC_VERSION})
        try:
            with self.opener(req, timeout=self.timeout) as r:
                data = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:  # never put the key or request body in the message
            raise VisionError(f"HTTP {e.code}") from None
        except (OSError, ValueError) as e:  # URLError, timeout, JSONDecodeError
            raise VisionError(type(e).__name__) from None
        for block in data.get("content") or []:
            if block.get("type") == "tool_use" and block.get("name") == "report_title":
                inp = block.get("input") or {}
                return VisionResult(text=str(inp.get("title") or "").strip(), readable=bool(inp.get("readable", True)))
        raise VisionError("no tool_use block in the answer")


NVIDIA_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
NVIDIA_KEY_ENVS = ("NEWSBAR_VISION_API_KEY", "NVIDIA_API_KEY")
_JSON_OBJ = re.compile(r"\{.*\}", re.S)
_PROMPT = ('Copy the main headline printed in this Taiwanese TV news title bar. Do not add, translate or fix words; '
           'ignore tickers and logos. Answer with JSON only: {"title": "<headline>", "readable": true}. '
           'If there is no readable headline: {"title": "", "readable": false}.')


def parse_title_answer(text: str) -> VisionResult:
    """Models without forced tool use answer in free text; take the JSON object if there is one,
    otherwise treat a short plain answer as the title. Anything else is an error, not a guess."""
    m = _JSON_OBJ.search(text or "")
    if m:
        try:
            obj = json.loads(m.group(0))
            return VisionResult(text=str(obj.get("title") or "").strip(), readable=bool(obj.get("readable", True)))
        except ValueError:
            pass
    plain = (text or "").strip().strip("\"'「」")
    if 0 < len(plain) <= 60 and "\n" not in plain:
        return VisionResult(text=plain, readable=True, note="plain-text answer")
    raise VisionError("unusable answer")


@dataclass
class OpenAICompatVision:
    """OpenAI-style chat completions with an image (NVIDIA's hosted models use this shape)."""

    api_key: str
    model: str
    timeout: float = 60.0
    url: str = NVIDIA_URL
    opener = staticmethod(urllib.request.urlopen)

    def read_title(self, image: bytes, media_type: str, ocr_text: str) -> VisionResult:
        data_uri = f"data:{media_type};base64,{base64.b64encode(image).decode('ascii')}"
        body = json.dumps({
            "model": self.model, "max_tokens": 2000, "temperature": 0,  # reasoning models spend tokens thinking first
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": _PROMPT},
                {"type": "image_url", "image_url": {"url": data_uri}},
            ]}],
        }).encode("utf-8")
        req = urllib.request.Request(self.url, data=body, method="POST", headers={
            "content-type": "application/json", "accept": "application/json", "authorization": f"Bearer {self.api_key}"})
        try:
            with self.opener(req, timeout=self.timeout) as r:
                data = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:  # never put the key or request body in the message
            raise VisionError(f"HTTP {e.code}") from None
        except (OSError, ValueError) as e:
            raise VisionError(type(e).__name__) from None
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise VisionError("no choices in the answer") from None
        if not isinstance(text, str) or not text.strip():
            raise VisionError("empty answer (a reasoning model may have run out of tokens)")
        return parse_title_answer(text)


def from_env(mode: str, env: dict | None = None, timeout: float = 30.0) -> VisionBackend | None:
    """mode: off | mock | anthropic | nvidia. Raises VisionConfigError with a plain message when something is missing."""
    env = os.environ if env is None else env
    if mode in (None, "", "off"):
        return None
    if mode == "mock":
        return MockVision()
    if mode == "nvidia":
        key = next((env[k] for k in NVIDIA_KEY_ENVS if env.get(k)), "")
        if not key:
            raise VisionConfigError(f"沒有 API 金鑰：請在環境變數設 {NVIDIA_KEY_ENVS[0]}（或 {NVIDIA_KEY_ENVS[1]}）；金鑰不要寫進檔案")
        model = env.get("NEWSBAR_VISION_MODEL", "")
        if not model:
            raise VisionConfigError("沒有指定模型：請設 NEWSBAR_VISION_MODEL（可用 GET /v1/models 列出，程式不寫死）")
        return OpenAICompatVision(api_key=key, model=model, timeout=max(timeout, 60.0), url=env.get("NEWSBAR_VISION_BASE_URL", NVIDIA_URL))
    if mode == "anthropic":
        key = next((env[k] for k in KEY_ENVS if env.get(k)), "")
        if not key:
            raise VisionConfigError(f"沒有 API 金鑰：請在環境變數設 {KEY_ENVS[0]}（或 {KEY_ENVS[1]}）；金鑰不要寫進檔案")
        model = env.get("NEWSBAR_VISION_MODEL", "")
        if not model:
            raise VisionConfigError("沒有指定模型：請設 NEWSBAR_VISION_MODEL（模型代號請查供應商文件，程式不寫死）")
        return AnthropicVision(api_key=key, model=model, timeout=timeout, url=env.get("NEWSBAR_VISION_BASE_URL", ANTHROPIC_URL))
    raise VisionConfigError(f"不認得的 mode：{mode}（可用 off / mock / anthropic）")


# ---------------------------------------------------------------- what to send

_NORM = re.compile(r"\s+")


def _norm(text: str | None) -> str:
    return _NORM.sub("", text or "")


def _bare(text: str | None) -> str:
    """For comparing two reads: no spaces, no punctuation or quote marks (models add 「」:"" that OCR drops)."""
    import unicodedata

    return "".join(ch for ch in _norm(text) if not unicodedata.category(ch).startswith(("P", "S")))


@dataclass
class TitleRun:
    channel: str
    start: int
    end: int  # last second (inclusive)
    text: str
    mean_conf: float
    frame: str | None = None  # the crop to send (see stable_runs)
    visible_secs: int | None = None  # seconds in the run where OCR itself read text; None = raw reads unknown

    @property
    def seconds(self) -> int:
        return self.end - self.start + 1


def _pick_frame(seg: list[dict], text: str, raw: dict[int, dict] | None) -> tuple[str | None, int | None]:
    """Which crop of a run to send.

    The title text of a second is a 5-second vote, so a second can carry a title while its own crop shows no
    title bar at all (anchor walking, map, transition). With the raw per-second OCR reads available, send a
    crop where OCR itself read text — the same text if possible, then the closest, the most confident and the
    nearest the middle. No such second → no crop (nothing worth sending). Without raw reads: the middle crop."""
    mid = len(seg) // 2
    if raw is None:
        frames = [r.get("frame") for r in seg[mid:] + seg[:mid] if r.get("frame")]
        return (frames[0] if frames else None), None
    want = _norm(text)
    best, best_key, visible = None, None, 0
    for i, r in enumerate(seg):
        got = _norm((raw.get(int(r.get("sec", 0))) or {}).get("text"))
        if not got:
            continue
        visible += 1
        if not r.get("frame"):
            continue
        conf = float((raw.get(int(r.get("sec", 0))) or {}).get("conf") or 0)
        key = (got == want, agreement(want, got)[1], conf, -abs(i - mid))
        if best_key is None or key > best_key:
            best, best_key = r.get("frame"), key
    return best, visible


def stable_runs(channel: str, rows: list[dict], min_sec: int = MIN_STABLE_SEC,
                raw: dict[int, dict] | None = None) -> list[TitleRun]:
    """Consecutive seconds with the same title text; runs shorter than min_sec are jitter or transitions.
    raw: per-second OCR reads before voting ({sec: {"text", "conf"}}, from ocr_raw.jsonl) for picking the crop."""
    out: list[TitleRun] = []
    cur: str | None = None
    first = 0

    def close(i_end: int) -> None:
        if cur and i_end - first >= min_sec:
            seg = rows[first:i_end]
            mid = len(seg) // 2
            text = seg[mid].get("bar_text") or cur
            frame, visible = _pick_frame(seg, text, raw)
            confs = [float(r.get("bar_conf") or 0) for r in seg]
            out.append(TitleRun(channel, int(seg[0].get("sec", first)), int(seg[-1].get("sec", i_end - 1)),
                                text, sum(confs) / len(confs), frame, visible))

    for i, r in enumerate(rows):
        t = _norm(r.get("bar_text"))
        if t != cur:
            close(i)
            cur, first = t, i
    close(len(rows))
    return out


def select_runs(runs: list[TitleRun], max_conf: float = LOW_CONF, limit: int | None = None) -> list[TitleRun]:
    """Only low-confidence stable titles that have a crop to send; lowest confidence first."""
    chosen = sorted((r for r in runs if r.frame and r.mean_conf < max_conf), key=lambda r: (r.mean_conf, r.channel, r.start))
    return chosen[:limit] if limit else chosen


def resolve_frame(frame: str, workdir: Path, channel: str) -> Path | None:
    p = Path(frame)
    for cand in (p, workdir.parent.parent / p, workdir / channel / "frames" / "main_bar" / p.name):
        if cand.is_file():
            return cand
    return None


def agreement(ocr_text: str, model_text: str) -> tuple[str, float]:
    """How much of the model's text OCR also read (character overlap, order-free). The model is only
    trusted as a re-read, never as a source of new words: a low ratio means 'differs', not 'better'."""
    a, b = _bare(ocr_text), _bare(model_text)
    if not a or not b:
        return "differs", 0.0
    pool = list(a)
    hit = 0
    for ch in b:
        if ch in pool:
            pool.remove(ch)
            hit += 1
    ratio = hit / max(len(a), len(b))
    return ("same" if ratio >= 0.95 else "close" if ratio >= 0.7 else "differs"), round(ratio, 3)


@dataclass
class CheckReport:
    candidates: int = 0
    no_bar: int = 0  # low-confidence titles skipped because OCR read nothing in any of their seconds
    total_bytes: int = 0
    sent: int = 0
    errors: int = 0
    verdicts: dict = field(default_factory=dict)
    rows: list = field(default_factory=list)


def _read_with_retry(backend: VisionBackend, image: bytes, ocr_text: str, tries: int = 2) -> VisionResult:
    """Hosted models sometimes answer empty or with a 5xx; one more try usually works. A 4xx (bad key,
    unknown model, rate limit) is not retried: it will not fix itself within seconds."""
    for attempt in range(tries):
        try:
            return backend.read_title(image, "image/jpeg", ocr_text)
        except VisionError as e:
            if attempt == tries - 1 or str(e).startswith("HTTP 4"):
                raise
    raise VisionError("unreachable")


def run_check(workdir: Path, channels: list[str], backend: VisionBackend | None, send: bool = False,
              max_conf: float = LOW_CONF, limit: int | None = None, say=print) -> CheckReport:
    """Dry run unless send=True AND a backend is given. Never raises for a single bad call."""
    rep = CheckReport()
    per_channel: list[list[tuple[TitleRun, Path]]] = []
    for ch in channels:
        obs = workdir / ch / "obs.jsonl"
        if not obs.is_file():
            say(f"[{ch}] 找不到 {obs}，略過")
            continue
        rows = [json.loads(line) for line in obs.open(encoding="utf-8")]
        raw_file = workdir / ch / "ocr_raw.jsonl"
        raw = None
        if raw_file.is_file():
            raw = {}
            for line in raw_file.open(encoding="utf-8"):
                r = json.loads(line)
                raw[int(r.get("sec", 0))] = r
        runs = stable_runs(ch, rows, raw=raw)
        rep.no_bar += sum(1 for r in runs if r.mean_conf < max_conf and r.visible_secs == 0)
        picked = []
        for run in select_runs(runs, max_conf, None):
            path = resolve_frame(run.frame, workdir, ch) if run.frame else None
            if path:
                picked.append((run, path))
        per_channel.append(picked)
    # round-robin across channels so a small --limit still samples every channel
    todo = [item for group in zip_longest(*per_channel) for item in group if item]
    todo = todo[:limit] if limit else todo
    rep.candidates = len(todo)
    rep.total_bytes = sum(p.stat().st_size for _, p in todo)
    say(f"低信心的穩定標題 {rep.candidates} 段，截圖共 {rep.total_bytes / 1024:.0f} KB（每段一張主標題裁切圖）"
        + (f"；另有 {rep.no_bar} 段整段都沒讀到標題條，不送" if rep.no_bar else ""))
    if not (send and backend):
        say("乾跑：沒有送出任何東西。要送出請加 --send，並先設好金鑰與模型（見 docs/vision.md）。")
        return rep
    for run, path in todo:
        try:
            res = _read_with_retry(backend, path.read_bytes(), run.text)
            verdict, ratio = agreement(run.text, res.text) if res.readable else ("unreadable", 0.0)
            rep.sent += 1
            row = {"channel": run.channel, "start": run.start, "end": run.end, "mean_conf": round(run.mean_conf, 3),
                   "frame": path.name, "visible_secs": run.visible_secs,
                   "ocr": run.text, "model": res.text, "readable": res.readable, "verdict": verdict, "overlap": ratio}
        except VisionError as e:
            rep.errors += 1
            verdict = "error"
            row = {"channel": run.channel, "start": run.start, "end": run.end, "ocr": run.text, "verdict": "error", "error": str(e)}
        rep.verdicts[verdict] = rep.verdicts.get(verdict, 0) + 1
        rep.rows.append(row)
    return rep


# ---------------------------------------------------------------- inside the labeling pipeline

import hashlib  # noqa: E402
import threading  # noqa: E402

DEFAULT_MIN_OVERLAP = 0.8  # accept a re-read only if it keeps at least this share of OCR's characters
DEFAULT_REVIEW_CONF = 0.4  # same threshold as TXT-10 (pipeline.OCR_REVIEW_CONF): titles OCR read badly


def backend_from_rules(rules: dict | None, env: dict | None = None) -> VisionBackend | None:
    """rules.yaml → vision.mode (off | mock | anthropic | nvidia); NEWSBAR_VISION_MODE overrides it.
    Off by default. A missing key or model raises VisionConfigError before any story is labelled."""
    env = os.environ if env is None else env
    mode = env.get("NEWSBAR_VISION_MODE") or ((rules or {}).get("vision") or {}).get("mode") or "off"
    return from_env(mode, env)


class ReadCache:
    """Answers keyed by (model, image hash): a rerun does not send the same crop again. Thread-safe."""

    def __init__(self, path: Path | None):
        self.path, self.lock = path, threading.Lock()
        self.data: dict[str, dict] = {}
        if path and path.is_file():
            self.data = json.loads(path.read_text(encoding="utf-8"))

    def key(self, backend: VisionBackend, image: bytes) -> str:
        return f"{getattr(backend, 'model', type(backend).__name__)}:{hashlib.sha1(image).hexdigest()}"

    def get(self, k: str) -> VisionResult | None:
        with self.lock:
            v = self.data.get(k)
        return VisionResult(v["text"], v["readable"], "cached") if v else None

    def put(self, k: str, res: VisionResult) -> None:
        with self.lock:
            self.data[k] = {"text": res.text, "readable": res.readable}
            if self.path:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")


def _best_crop(title: str, obs: list) -> Path | None:
    """The story second that shows this title most confidently (and has a crop on disk)."""
    want = _norm(title)
    cands = [(float(getattr(o, "bar_conf", 0) or 0), -abs(i - len(obs) // 2), o.frame)
             for i, o in enumerate(obs) if _norm(getattr(o, "bar_text", None)) == want and getattr(o, "frame", None)]
    for _, _, frame in sorted(cands, reverse=True):
        if Path(frame).is_file():
            return Path(frame)
    return None


def reread_titles(titles: list[str], conf_of: dict[str, float] | None, obs: list, backend: VisionBackend,
                  rules: dict | None = None, cache: ReadCache | None = None) -> tuple[list[str], set[int], list[dict]]:
    """Re-read the low-confidence titles of one story from their crops.

    Returns (titles, indexes of titles replaced, records). A re-read replaces the OCR title only when the model
    could read the bar and kept at least min_overlap of OCR's characters: the model may fix misread characters
    but may not bring in a different headline. Errors never stop labeling; the OCR title is kept."""
    cfg = (rules or {}).get("vision") or {}
    min_overlap = float(cfg.get("min_overlap", DEFAULT_MIN_OVERLAP))
    review_conf = float(cfg.get("review_conf", DEFAULT_REVIEW_CONF))
    out, fixed, recs = list(titles), set(), []
    for i, t in enumerate(titles[: int(cfg.get("max_titles", 2))]):
        conf = (conf_of or {}).get(t)
        if conf is None or conf > review_conf:
            continue
        crop = _best_crop(t, obs)
        rec = {"ocr": t, "conf": round(conf, 3), "frame": crop.name if crop else None}
        if crop is None:
            recs.append({**rec, "status": "no_crop"})
            continue
        image = crop.read_bytes()
        k = cache.key(backend, image) if cache else None
        try:
            res = (cache.get(k) if cache else None) or _read_with_retry(backend, image, t)
            if cache and res.note != "cached":
                cache.put(k, res)
        except VisionError as e:
            recs.append({**rec, "status": "error", "error": str(e)})
            continue
        verdict, ratio = agreement(t, res.text) if res.readable else ("unreadable", 0.0)
        ok = res.readable and bool(res.text) and ratio >= min_overlap and _norm(res.text) != _norm(t)
        if ok:
            out[i] = res.text
            fixed.add(i)
        recs.append({**rec, "model": res.text, "verdict": verdict, "overlap": ratio,
                     "status": "replaced" if ok else ("same" if _norm(res.text) == _norm(t) else "kept_ocr")})
    return out, fixed, recs
