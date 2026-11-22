"""Local web UI for operators (`newsbar serve`): pick recordings, run the pipeline, watch
progress, read the result, download the sheet.

Standard library only. Binds 127.0.0.1 by default: recordings and sheets stay on the
machine (NFR-14). Each job runs `newsbar run` in a subprocess and its output lines are
parsed into per-channel stages, so the page shows the real backend, not a simulation.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import unquote

import yaml

CHANNELS = ["tvbs", "ettv", "ftv", "cts"]
VIDEO_EXT = {".ts", ".mp4", ".mkv", ".mov"}
STAGE_RE = re.compile(r"^\[(?P<ch>[a-z0-9_]+)(?:\.[a-z0-9]+)?\]\s*(?P<msg>.+?)\s*(?:…)?$")
DONE_RE = re.compile(r"^(?P<ch>[a-z0-9_]+): (?P<cells>\d+) 格")
WEB = Path(__file__).with_name("web")


@dataclass
class Job:
    id: str
    cmd: list[str]
    out: Path
    started: float = field(default_factory=time.time)
    lines: list[tuple[float, str]] = field(default_factory=list)
    stages: dict[str, str] = field(default_factory=dict)
    returncode: int | None = None
    finished: float | None = None
    videos: dict[str, Path] = field(default_factory=dict)

    def feed(self, line: str) -> None:
        line = line.rstrip()
        if not line or "ctranslate2" in line:
            return
        self.lines.append((round(time.time() - self.started, 1), line))
        if m := DONE_RE.match(line):
            self.stages[m["ch"]] = "完成"
        elif m := STAGE_RE.match(line):
            msg = m["msg"]
            self.stages[m["ch"]] = "沿用已抽取的資料" if msg.startswith("沿用") else msg

    @property
    def state(self) -> str:
        if self.returncode is None:
            return "running"
        return "done" if self.returncode == 0 and self.out.exists() else "failed"

    def as_dict(self, tail: int = 40) -> dict:
        end = self.finished or time.time()
        return {"id": self.id, "state": self.state, "elapsed": round(end - self.started, 1),
                "stages": self.stages, "lines": self.lines[-tail:], "returncode": self.returncode}


class Jobs:
    def __init__(self, launch: Callable[[list[str]], subprocess.Popen] | None = None):
        self.items: dict[str, Job] = {}
        self.lock = threading.Lock()
        self.launch = launch or (lambda cmd: subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
            env={**os.environ, "PYTHONUNBUFFERED": "1"}))

    def start(self, cmd: list[str], out: Path) -> Job:
        job = Job(uuid.uuid4().hex[:8], cmd, out)
        for k, v in zip(cmd, cmd[1:]):
            if k == "--video" and "=" in v:
                ch, path = v.split("=", 1)
                job.videos[ch] = Path(path.split("@")[0])
        with self.lock:
            self.items[job.id] = job

        def pump() -> None:
            try:
                p = self.launch(cmd)
                for line in p.stdout:
                    job.feed(line)
                job.returncode = p.wait()
            except Exception as e:  # report the failure on the page instead of hanging
                job.feed(f"[錯誤] {e}")
                job.returncode = -1
            job.finished = time.time()

        threading.Thread(target=pump, daemon=True).start()
        return job

    def get(self, job_id: str) -> Job | None:
        with self.lock:
            return self.items.get(job_id)


@dataclass
class Settings:
    video_dir: Path
    template: Path
    out_dir: Path
    channels: Path
    rules: Path
    rules_fast: Path | None
    workdir: Path
    asr: str = "faster-whisper"
    jobs: int = 4
    python: str = sys.executable
    judge_ask: Callable[[str, str], dict] | None = None  # tests inject a fake; default is the local Ollama


def list_inputs(s: Settings) -> dict:
    files = sorted(p.name for p in s.video_dir.iterdir() if p.suffix.lower() in VIDEO_EXT) if s.video_dir.exists() else []
    guess = {ch: next((f for f in files if f.lower().startswith(ch)), None) for ch in CHANNELS}
    return {"files": files, "guess": guess, "template": s.template.name if s.template.exists() else None,
            "fast_available": bool(s.rules_fast and s.rules_fast.exists())}


def build_cmd(s: Settings, req: dict, out: Path) -> list[str]:
    videos = req.get("videos") or {}
    cmd = [s.python, "-m", "newsbar.cli", "run", "--template", str(s.template), "--out", str(out),
           "--channels", str(s.channels), "--asr", s.asr, "--jobs", str(s.jobs)]
    for ch in CHANNELS:
        name = videos.get(ch)
        if not name:
            continue
        path = (s.video_dir / name).resolve()
        if path.parent != s.video_dir.resolve() or path.suffix.lower() not in VIDEO_EXT or not path.exists():
            raise ValueError(f"找不到 {ch} 的影片：{name}")
        cmd += ["--video", f"{ch}={path}"]
    if "--video" not in cmd:
        raise ValueError("至少要選一台的影片")
    fast = bool(req.get("fast")) and s.rules_fast is not None
    cmd += ["--rules", str(s.rules_fast if fast else s.rules), "--workdir", str(s.workdir)]
    if req.get("resume"):
        cmd.append("--resume")
    return cmd


def _steady_seconds(workdir: Path, ch: str) -> list[tuple[int, int, str]]:
    """(sec, frame index, bar text) per second from the job's obs.jsonl, for picking screenshots."""
    f = workdir / ch / "obs.jsonl"
    if not f.exists():
        return []
    rows = []
    for line in f.read_text().splitlines():
        o = json.loads(line)
        if (idx := _frame_index(o.get("frame"))) is not None:
            rows.append((o["sec"], idx, (o.get("bar_text") or "").strip()))
    return rows


def shot_index(rows: list[tuple[int, int, str]], item: dict, end: int) -> int | None:
    """The second worth showing: the title held on the bar for 3 s (not the switch-over
    second, which is often blank); a few seconds into an ad break."""
    start = item.get("start_sec") or 0
    win = [r for r in rows if start <= r[0] < end]
    if not win:
        return _frame_index(item.get("frame"))
    if item.get("type") == "ad":
        return win[min(8, len(win) - 1)][1]
    want = (item.get("bar_text") or "").strip()
    for k in range(len(win) - 2):
        t = win[k][2]
        if t and win[k + 1][2] == t and win[k + 2][2] == t and (not want or t == want):
            return win[k + 1][1]
    return next((r[1] for r in win if r[2] and r[2] == want), _frame_index(item.get("frame")))


def ad_strip(rows: list[tuple[int, int, str]], start: int, end: int) -> list[list[int]]:
    """Four (sec, frame index) pairs spread across an ad break: one picture can land on the
    pre-break teaser, four show what the break really was."""
    win = [r for r in rows if start <= r[0] < end]
    if not win:
        return []
    return [[win[min(int(len(win) * f), len(win) - 1)][0], win[min(int(len(win) * f), len(win) - 1)][1]]
            for f in (0.05, 0.35, 0.65, 0.95)]


def read_result(out: Path, workdir: Path | None = None) -> dict:
    ev = out.with_suffix(".evidence.jsonl")
    rep = out.with_suffix(".report.json")
    cells = [json.loads(l) for l in ev.read_text().splitlines() if l.strip()] if ev.exists() else []
    report = json.loads(rep.read_text()) if rep.exists() else {}
    rows = {ch: _steady_seconds(workdir, ch) for ch in {c["channel"] for c in cells}} if workdir else {}
    starts = {ch: sorted(i.get("start_sec") or 0 for c in cells if c["channel"] == ch for i in c.get("items", []))
              for ch in rows}
    for c in cells:
        for i in c.get("items", []):
            s0 = i.get("start_sec") or 0
            end = next((x for x in starts.get(c["channel"], []) if x > s0), 10**6)
            i["_idx"] = shot_index(rows.get(c["channel"], []), i, end) if rows else _frame_index(i.get("frame"))
            i["_strip"] = ad_strip(rows.get(c["channel"], []), s0, end) if i.get("type") == "ad" else []
    judged = {(j["channel"], j["start"]): j for j in load_judge(out)}
    return {"cells": [{"ch": c["channel"], "m": c["minute"], "v": c["value"], "conf": c.get("confidence"),
                       "fb": c.get("fallback"), "sig": c.get("signals", []),
                       "items": [{"type": i["type"], "label": i.get("label"), "start": i.get("start_sec"),
                                  "bar": i.get("bar_text"), "asr": (i.get("asr_text") or "")[:400] or None,
                                  "idx": i.get("_idx"), "strip": i.get("_strip", []),
                                  "judge": ({"verdict": j["verdict"], "reason": j["reason"], "prev": j.get("prev")}
                                            if (j := judged.get((c["channel"], i.get("start_sec")))) else None)}
                                 for i in c.get("items", [])]} for c in cells],
            "report": report.get("channels", {}), "timings": report.get("timings_sec", {}),
            "failed": report.get("failed_channels", {}), "clock": report.get("clock", {})}


def judge_path(out: Path) -> Path:
    return out.with_suffix(".judge.json")


def load_judge(out: Path) -> list[dict]:
    p = judge_path(out)
    return json.loads(p.read_text(encoding="utf-8")).get("items", []) if p.exists() else []


def run_judge(job: Job, s: Settings) -> dict:
    """LLM second opinion on FB-1 cells; saved beside the sheet, never written into it."""
    from . import judge

    ev = job.out.with_suffix(".evidence.jsonl")
    if not ev.exists():
        raise ValueError("還沒有判斷依據檔")
    rows = [json.loads(l) for l in ev.read_text(encoding="utf-8").splitlines() if l.strip()]
    lab = (yaml.safe_load(s.rules.read_text(encoding="utf-8")) or {}).get("labeler", {}) if s.rules.exists() else {}
    ask = s.judge_ask or judge.ollama_ask(lab.get("host", "http://127.0.0.1:11434"), lab.get("model", "qwen2.5:7b-instruct"))
    try:
        items = judge.judge_changes(rows, ask)
    except (OSError, ValueError, KeyError) as e:  # Ollama down, bad JSON
        raise RuntimeError(f"地端模型沒有回應：{e}") from e
    judge_path(job.out).write_text(json.dumps({"model": lab.get("model"), "items": items}, ensure_ascii=False), encoding="utf-8")
    cleared = sum(1 for j in items if j["verdict"] == "different")
    return {"checked": len(items), "cleared": cleared}


def _frame_index(frame: str | None) -> int | None:
    """Evidence frames are named by the recording's second (…/main_bar/00123.jpg)."""
    m = re.search(r"(\d+)\.jpg$", frame or "")
    return int(m[1]) if m else None


def full_frame(video: Path, idx: int, cache: Path) -> Path:
    """One full picture at second `idx` of the recording, cut on demand and kept beside the job."""
    out = cache / f"{video.stem}_{idx:05d}.jpg"
    if not out.exists():
        cache.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", str(idx + 0.5), "-i", str(video),
                        "-frames:v", "1", "-vf", "scale=960:-2", "-q:v", "4", str(out)],
                       check=True, timeout=60)
    return out


ERROR_TYPES = ["漏換題", "過度換題", "包框誤判", "角落廣告誤判", "標不可辨認", "捏造", "格式錯誤", "其他"]


def corrections_path(job: Job) -> Path:
    return job.out.parent / "corrections.json"


def load_corrections(job: Job) -> list[dict]:
    p = corrections_path(job)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def save_correction(job: Job, req: dict) -> list[dict]:
    """One record per (channel, minute); the newest review of a cell replaces the older one.
    Same fields as the review page's export, so `newsbar apply-corrections` reads it too."""
    ch, minute = str(req.get("channel", "")), str(req.get("minute", ""))
    if ch not in job.videos or not re.fullmatch(r"\d{4}", minute):
        raise ValueError("台別或分鐘不正確")
    reviewer = str(req.get("reviewer") or "").strip()
    if not reviewer:
        raise ValueError("請先填審核者")
    verdict = req.get("verdict")
    rows = [c for c in load_corrections(job) if (c["channel"], c["minute"]) != (ch, minute)]
    if verdict in ("ok", "fix"):
        correct = str(req.get("correct") or "").strip() if verdict == "fix" else None
        if verdict == "fix" and req.get("error_type") not in ERROR_TYPES:
            raise ValueError("請選錯誤類型")
        rows.append({"channel": ch, "minute": minute, "verdict": verdict, "correct": correct,
                     "error_type": req.get("error_type") if verdict == "fix" else None,
                     "original": req.get("original"), "note": str(req.get("note") or "")[:200] or None,
                     "reviewer": reviewer, "reviewed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
    elif verdict != "remove":
        raise ValueError("verdict 只能是 ok、fix 或 remove")
    rows.sort(key=lambda c: (c["minute"], c["channel"]))
    corrections_path(job).write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    return rows


def write_corrected(job: Job, s: Settings) -> Path:
    """FR-16: a separate corrected sheet; the delivered one is never edited."""
    import yaml

    from . import evaluate, excel_writer, review
    from .schemas import Cell

    cfg = yaml.safe_load(s.channels.read_text(encoding="utf-8"))["channels"]
    headers = {k: v["excel_column_header"] for k, v in cfg.items() if k in job.videos}
    cells = evaluate.read_sheet(job.out, headers)
    fixed = review.apply_corrections(cells, load_corrections(job))
    as_cells = {ch: {m: Cell(ch, m, v, []) for m, v in col.items() if v} for ch, col in fixed.items()}
    out = job.out.with_name(job.out.stem + ".修正版.xlsx")
    excel_writer.write(s.template, out, as_cells, {k: headers[k] for k in as_cells})
    return out


def make_handler(s: Settings, jobs: Jobs):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # keep the console for job output
            pass

        def send(self, code: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def json(self, obj, code: int = 200) -> None:
            self.send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def job_or_404(self, job_id: str) -> Job | None:
            job = jobs.get(job_id)
            if job is None:
                self.json({"error": "找不到這個工作"}, 404)
            return job

        def do_GET(self):
            path = unquote(self.path.split("?")[0])
            if path in ("/", "/index.html"):
                return self.send(200, (WEB / "app.html").read_bytes(), "text/html; charset=utf-8")
            if path == "/api/inputs":
                return self.json(list_inputs(s))
            m = re.fullmatch(r"/api/jobs/([0-9a-f]{8})/(bar|frame)/([a-z0-9_]+)/(\d{1,6})", path)
            if m:
                job = self.job_or_404(m[1])
                if job is None:
                    return None
                ch, idx = m[3], int(m[4])
                if ch not in job.videos:
                    return self.json({"error": "這個工作沒有這一台"}, 404)
                if m[2] == "bar":
                    img = s.workdir / ch / "frames" / "main_bar" / f"{idx:05d}.jpg"
                else:
                    try:
                        img = full_frame(job.videos[ch], idx, job.out.parent / "frames")
                    except (subprocess.SubprocessError, OSError) as e:
                        return self.json({"error": f"截圖失敗：{e}"}, 500)
                if not img.exists():
                    return self.json({"error": "沒有這一秒的畫面"}, 404)
                return self.send(200, img.read_bytes(), "image/jpeg")
            m = re.fullmatch(r"/api/jobs/([0-9a-f]{8})/(corrections|corrected\.xlsx)", path)
            if m:
                job = self.job_or_404(m[1])
                if job is None:
                    return None
                if m[2] == "corrections":
                    return self.json({"items": load_corrections(job), "error_types": ERROR_TYPES})
                fixed = job.out.with_name(job.out.stem + ".修正版.xlsx")
                if not fixed.exists():
                    return self.json({"error": "還沒產出修正版"}, 404)
                return self.send(200, fixed.read_bytes(),
                                 "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                 {"Content-Disposition": f"attachment; filename=\"corrected.xlsx\"; filename*=UTF-8''{_q(fixed.name)}"})
            m = re.fullmatch(r"/api/jobs/([0-9a-f]{8})(/result|/xlsx|/review)?", path)
            if not m:
                return self.json({"error": "not found"}, 404)
            job = self.job_or_404(m[1])
            if job is None:
                return None
            if m[2] is None:
                return self.json(job.as_dict())
            if job.state != "done":
                return self.json({"error": "工作尚未完成"}, 409)
            if m[2] == "/result":
                return self.json(read_result(job.out, s.workdir))
            if m[2] == "/xlsx":
                return self.send(200, job.out.read_bytes(),
                                 "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                 {"Content-Disposition": f"attachment; filename=\"12jiankan.xlsx\"; filename*=UTF-8''{_q(job.out.name)}"})
            review = job.out.with_suffix(".review.html")
            if not review.exists():
                return self.json({"error": "沒有抽查頁"}, 404)
            return self.send(200, review.read_bytes(), "text/html; charset=utf-8")

        def do_POST(self):
            m = re.fullmatch(r"/api/jobs/([0-9a-f]{8})/judge", self.path)
            if m:
                job = self.job_or_404(m[1])
                if job is None:
                    return None
                if job.state != "done":
                    return self.json({"error": "工作尚未完成"}, 409)
                try:
                    return self.json(run_judge(job, s))
                except ValueError as e:
                    return self.json({"error": str(e)}, 400)
                except RuntimeError as e:
                    return self.json({"error": str(e)}, 503)
            m = re.fullmatch(r"/api/jobs/([0-9a-f]{8})/(corrections|corrected)", self.path)
            if m:
                job = self.job_or_404(m[1])
                if job is None:
                    return None
                if job.state != "done":
                    return self.json({"error": "工作尚未完成"}, 409)
                try:
                    if m[2] == "corrections":
                        req = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                        return self.json({"items": save_correction(job, req)})
                    out = write_corrected(job, s)
                    return self.json({"file": out.name, "count": len(load_corrections(job))})
                except (ValueError, json.JSONDecodeError) as e:
                    return self.json({"error": str(e)}, 400)
            if self.path != "/api/jobs":
                return self.json({"error": "not found"}, 404)
            try:
                req = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                job_dir = s.out_dir / time.strftime("%Y%m%d_%H%M%S")
                job_dir.mkdir(parents=True, exist_ok=True)
                out = job_dir / "12監看.xlsx"
                job = jobs.start(build_cmd(s, req, out), out)
            except (ValueError, json.JSONDecodeError) as e:
                return self.json({"error": str(e)}, 400)
            return self.json(job.as_dict(), 201)

    return H


def _q(text: str) -> str:
    from urllib.parse import quote
    return quote(text)


def serve(s: Settings, host: str = "127.0.0.1", port: int = 8765, jobs: Jobs | None = None) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((host, port), make_handler(s, jobs or Jobs()))
    return httpd
