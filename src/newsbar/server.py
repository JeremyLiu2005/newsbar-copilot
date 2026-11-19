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


def read_result(out: Path) -> dict:
    ev = out.with_suffix(".evidence.jsonl")
    rep = out.with_suffix(".report.json")
    cells = [json.loads(l) for l in ev.read_text().splitlines() if l.strip()] if ev.exists() else []
    report = json.loads(rep.read_text()) if rep.exists() else {}
    return {"cells": [{"ch": c["channel"], "m": c["minute"], "v": c["value"], "conf": c.get("confidence"),
                       "fb": c.get("fallback"), "sig": c.get("signals", []),
                       "items": [{"type": i["type"], "label": i.get("label"), "start": i.get("start_sec"),
                                  "bar": i.get("bar_text")} for i in c.get("items", [])]} for c in cells],
            "report": report.get("channels", {}), "timings": report.get("timings_sec", {})}


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
                return self.json(read_result(job.out))
            if m[2] == "/xlsx":
                return self.send(200, job.out.read_bytes(),
                                 "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                 {"Content-Disposition": f"attachment; filename=\"12jiankan.xlsx\"; filename*=UTF-8''{_q(job.out.name)}"})
            review = job.out.with_suffix(".review.html")
            if not review.exists():
                return self.json({"error": "沒有抽查頁"}, 404)
            return self.send(200, review.read_bytes(), "text/html; charset=utf-8")

        def do_POST(self):
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
