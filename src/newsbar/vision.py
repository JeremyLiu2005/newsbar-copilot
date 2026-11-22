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


def from_env(mode: str, env: dict | None = None, timeout: float = 30.0) -> VisionBackend | None:
    """mode: off | mock | anthropic. Raises VisionConfigError with a plain message when something is missing."""
    env = os.environ if env is None else env
    if mode in (None, "", "off"):
        return None
    if mode == "mock":
        return MockVision()
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


@dataclass
class TitleRun:
    channel: str
    start: int
    end: int  # last second (inclusive)
    text: str
    mean_conf: float
    frame: str | None = None  # a crop from the middle of the run

    @property
    def seconds(self) -> int:
        return self.end - self.start + 1


def stable_runs(channel: str, rows: list[dict], min_sec: int = MIN_STABLE_SEC) -> list[TitleRun]:
    """Consecutive seconds with the same title text; runs shorter than min_sec are jitter or transitions."""
    out: list[TitleRun] = []
    cur: str | None = None
    first = 0

    def close(i_end: int) -> None:
        if cur and i_end - first >= min_sec:
            seg = rows[first:i_end]
            mid = len(seg) // 2
            frames = [r.get("frame") for r in seg[mid:] + seg[:mid] if r.get("frame")]
            confs = [float(r.get("bar_conf") or 0) for r in seg]
            out.append(TitleRun(channel, int(seg[0].get("sec", first)), int(seg[-1].get("sec", i_end - 1)),
                                seg[mid].get("bar_text") or cur, sum(confs) / len(confs), frames[0] if frames else None))

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
    a, b = _norm(ocr_text), _norm(model_text)
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
    total_bytes: int = 0
    sent: int = 0
    errors: int = 0
    verdicts: dict = field(default_factory=dict)
    rows: list = field(default_factory=list)


def run_check(workdir: Path, channels: list[str], backend: VisionBackend | None, send: bool = False,
              max_conf: float = LOW_CONF, limit: int | None = None, say=print) -> CheckReport:
    """Dry run unless send=True AND a backend is given. Never raises for a single bad call."""
    rep = CheckReport()
    todo: list[tuple[TitleRun, Path]] = []
    for ch in channels:
        obs = workdir / ch / "obs.jsonl"
        if not obs.is_file():
            say(f"[{ch}] 找不到 {obs}，略過")
            continue
        rows = [json.loads(line) for line in obs.open(encoding="utf-8")]
        for run in select_runs(stable_runs(ch, rows), max_conf, None):
            path = resolve_frame(run.frame, workdir, ch) if run.frame else None
            if path:
                todo.append((run, path))
    todo = todo[:limit] if limit else todo
    rep.candidates = len(todo)
    rep.total_bytes = sum(p.stat().st_size for _, p in todo)
    say(f"低信心的穩定標題 {rep.candidates} 段，截圖共 {rep.total_bytes / 1024:.0f} KB（每段一張主標題裁切圖）")
    if not (send and backend):
        say("乾跑：沒有送出任何東西。要送出請加 --send，並先設好金鑰與模型（見 docs/vision.md）。")
        return rep
    for run, path in todo:
        try:
            res = backend.read_title(path.read_bytes(), "image/jpeg", run.text)
            verdict, ratio = agreement(run.text, res.text) if res.readable else ("unreadable", 0.0)
            rep.sent += 1
            row = {"channel": run.channel, "start": run.start, "end": run.end, "mean_conf": round(run.mean_conf, 3),
                   "ocr": run.text, "model": res.text, "readable": res.readable, "verdict": verdict, "overlap": ratio}
        except VisionError as e:
            rep.errors += 1
            verdict = "error"
            row = {"channel": run.channel, "start": run.start, "end": run.end, "ocr": run.text, "verdict": "error", "error": str(e)}
        rep.verdicts[verdict] = rep.verdicts.get(verdict, 0) + 1
        rep.rows.append(row)
    return rep
