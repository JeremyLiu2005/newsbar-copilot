"""Run manifest and extraction versions (可驗證、穩定).

Every run writes `<out>.manifest.json`: run_id, code commit, config hashes, input fingerprints and
model names. Each evidence / story record carries the run_id, so a cell can be traced to the exact
code, settings and video that produced it.

`--resume` reuses a channel's obs.jsonl only when the extraction inputs are unchanged (same video,
same channel ROI settings, same OCR/ASR backends). obs.meta.json beside it records them. An older
obs.jsonl without a meta file is still reused, with a warning that its version cannot be checked.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1.0"
_CHUNK = 4 * 1024 * 1024


def file_fingerprint(path: str | Path) -> dict[str, Any]:
    """Size plus SHA-256 of the first and last 4 MB: identifies a 1.4 GB recording in milliseconds
    (a full hash would cost ~10 s per channel). Missing file → {"missing": True}."""
    p = Path(path)
    if not p.exists():
        return {"path": str(p), "missing": True}
    size = p.stat().st_size
    h = hashlib.sha256()
    with open(p, "rb") as f:
        h.update(f.read(_CHUNK))
        if size > 2 * _CHUNK:
            f.seek(size - _CHUNK)
            h.update(f.read(_CHUNK))
    return {"path": str(p), "size": size, "sha256_head_tail": h.hexdigest()}


def config_hash(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:16]


def git_commit(cwd: str | Path | None = None) -> dict[str, Any]:
    def run(*args: str) -> str | None:
        try:
            r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            return None
        return r.stdout.strip() if r.returncode == 0 else None

    commit = run("rev-parse", "HEAD")
    status = run("status", "--porcelain", "--untracked-files=no")
    return {"commit": commit, "dirty": bool(status) if status is not None else None}


def extract_meta(video: str | Path, channel_cfg: dict, backend: str | None, asr: str | None,
                 asr_model: str | None, duration: float | None) -> dict[str, Any]:
    """What an obs.jsonl depends on. The rules file is not part of it: rules only change decisions."""
    fp = file_fingerprint(video)
    return {
        "video": {k: v for k, v in fp.items() if k != "path"},
        "channel_cfg": config_hash(channel_cfg),
        "ocr_backend": backend,
        "asr": asr,
        "asr_model": asr_model,
        "duration": duration,
    }


def meta_path(work: Path, ch: str) -> Path:
    return work / ch / "obs.meta.json"


def save_meta(work: Path, ch: str, meta: dict) -> None:
    p = meta_path(work, ch)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")


def resume_check(work: Path, ch: str, meta: dict) -> tuple[bool, str | None]:
    """(reuse obs.jsonl?, warning). Reuse when there is no meta (old run, warn) or it matches;
    re-extract when any recorded input differs."""
    if not (work / ch / "obs.jsonl").exists():
        return False, None
    p = meta_path(work, ch)
    if not p.exists():
        return True, "沿用的擷取結果沒有版本紀錄（舊版產生），無法確認影片與設定是否相同"
    try:
        old = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False, "擷取結果的版本紀錄讀不到，重新擷取"
    diff = [k for k in meta if old.get(k) != meta[k]]
    if diff:
        return False, "影片或擷取設定和上次不同（" + "、".join(diff) + "），重新擷取"
    failed = [k for k, v in load_stages(work, ch).items() if v.get("status") == "FAILED"]
    if failed:  # RUN-3: a degraded extraction (e.g. ASR timed out) is not reused
        return False, "上次擷取有步驟失敗（" + "、".join(failed) + "），重新擷取"
    return True, None


def load_stages(work: Path, ch: str) -> dict:
    p = work / ch / "stages.json"
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except (OSError, ValueError):
        return {}


def classify_error(e: BaseException) -> str:
    """RUN-3: INPUT (bad or missing file — fix the input, retrying will not help), TRANSIENT
    (timeout / connection — retrying may help), BUG (anything else — keep the message, report it)."""
    import subprocess as sp

    if isinstance(e, (FileNotFoundError, IsADirectoryError, PermissionError, sp.CalledProcessError, UnicodeDecodeError)):
        return "INPUT"
    if isinstance(e, (TimeoutError, ConnectionError, sp.TimeoutExpired)):
        return "TRANSIENT"
    return "BUG"


def new_run_id(now: dt.datetime | None = None) -> str:
    now = now or dt.datetime.now()
    return now.strftime("run_%Y%m%d_%H%M%S")


def manifest(run_id: str, *, started: str, finished: str, inputs: dict[str, dict], template: str | Path,
             channels_cfg: dict, rules: dict, lexicon_path: str | None, ocr_backend: str | None,
             asr: str | None, asr_model: str | None, llm_model: str | None, llm_state: str,
             resumed: dict[str, str], out_files: list[str], extraction_run_id: str | None = None,
             cache: dict | None = None) -> dict[str, Any]:
    """RUN-2: one manifest per run. A reused extraction without its own version record is stated as
    「來源版本不完整」 — no version number is made up for it."""
    lex_hash = None
    if lexicon_path and Path(lexicon_path).exists():
        lex_hash = hashlib.sha256(Path(lexicon_path).read_bytes()).hexdigest()[:16]
    return {
        "run_id": run_id,
        "schema_version": SCHEMA_VERSION,
        "started_at": started,
        "finished_at": finished,
        "code": git_commit(),
        "rules_version": str(rules.get("version", "0")),
        "config_hash": {"channels": config_hash(channels_cfg), "rules": config_hash(rules), "lexicon": lex_hash},
        "inputs": {ch: {k: v for k, v in fp.items()} for ch, fp in inputs.items()},
        "template": file_fingerprint(template),
        "models": {"ocr_backend": ocr_backend, "asr": asr, "asr_model": asr_model, "llm_model": llm_model,
                   "llm_state": llm_state},
        "extraction": resumed,
        "extraction_run_id": extraction_run_id or "來源版本不完整（沿用的擷取結果沒有版本紀錄）",
        "cache": cache or {"used": False},
        "label_rules": _label_rules(rules),
        "prompt_version": _prompt_version(),
        "outputs": out_files,
    }


def _label_rules(rules: dict) -> dict:
    from .labeler import LabelRules
    r = LabelRules.from_rules(rules)
    return {"min_len": r.min_len, "max_len": r.max_len, "target_len": r.target_len,
            "retries": int(((rules or {}).get("labeler") or {}).get("retries", 2))}


def _prompt_version() -> str:
    from . import llm
    return llm.PROMPT_VERSION
