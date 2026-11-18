"""History search, rule-version rollback and the daily report.

* search (NFR-27): grep-like search over every *.evidence.jsonl under a root —
  by keyword (label, bar text, speech), channel and date range.
* rules rollback (FR-21): eval results are appended to eval_history.jsonl; when
  the change-F1 of the last `weeks` ISO weeks all fall below `threshold`, the
  previous rules version becomes active again. Still fully automatic — only
  the thresholds go back, never a human into the loop.
* daily report (FR-18): one HTML page from the day's *.report.json files; an
  optional SMTP sender is provided but only runs when configured.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import re
import shutil
from email.message import EmailMessage
from pathlib import Path

_DATE = re.compile(r"(\d{8})")


# ---------- search ----------

def _date_of(path: Path) -> str | None:
    m = _DATE.search(path.name)
    return m[1] if m else None


def search(root: Path, q: str | None = None, channel: str | None = None,
           date_from: str | None = None, date_to: str | None = None) -> list[dict]:
    out = []
    for f in sorted(root.rglob("*.evidence.jsonl")):
        d = _date_of(f)
        if (date_from and (not d or d < date_from)) or (date_to and (not d or d > date_to)):
            continue
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if channel and r["channel"] != channel:
                continue
            hay = " ".join([r["value"]] + [(i.get("bar_text") or "") + " " + (i.get("asr_text") or "") for i in r["items"]])
            if q and q not in hay:
                continue
            out.append({"date": d, "channel": r["channel"], "minute": r["minute"], "value": r["value"],
                        "bar_text": next((i.get("bar_text") for i in r["items"] if i.get("bar_text")), None),
                        "file": str(f)})
    return out


# ---------- rules rollback ----------

def record_eval(history: Path, rules_version: str, change_f1: float, ad_f1: float, day: str) -> None:
    history.parent.mkdir(parents=True, exist_ok=True)
    with history.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"date": day, "rules_version": rules_version, "change_f1": change_f1, "ad_f1": ad_f1}) + "\n")


def _week(day: str) -> tuple[int, int]:
    y, w, _ = dt.date(int(day[:4]), int(day[4:6]), int(day[6:])).isocalendar()
    return y, w


def should_rollback(history: Path, rules_version: str, threshold: float = 0.75, weeks: int = 2) -> bool:
    if not history.exists():
        return False
    rows = [json.loads(x) for x in history.read_text(encoding="utf-8").splitlines() if x.strip()]
    rows = [r for r in rows if r["rules_version"] == rules_version]
    by_week: dict = {}
    for r in rows:
        by_week.setdefault(_week(r["date"]), []).append(r["change_f1"])
    if len(by_week) < weeks:
        return False
    last = sorted(by_week)[-weeks:]
    return all(sum(by_week[w]) / len(by_week[w]) < threshold for w in last)


def rollback(rules_dir: Path, active: Path, current_version: str, log: Path) -> str | None:
    """rules_dir holds v{version}.yaml snapshots; copy the one before current into `active`."""
    versions = sorted((p.stem[1:] for p in rules_dir.glob("v*.yaml")), key=lambda v: [int(x) for x in v.split(".")])
    if current_version not in versions or versions.index(current_version) == 0:
        return None
    prev = versions[versions.index(current_version) - 1]
    shutil.copy(rules_dir / f"v{prev}.yaml", active)
    with log.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"at": dt.datetime.now().isoformat(timespec="seconds"),
                            "from": current_version, "to": prev, "reason": "FR-21 週 F1 連續未達門檻"}) + "\n")
    return prev


# ---------- daily report ----------

def daily_report(reports: list[Path], out: Path, day: str) -> Path:
    rows = []
    alerts = []
    for p in sorted(reports):
        rep = json.loads(p.read_text(encoding="utf-8"))
        slot = p.name.split(".")[0]
        for ch, d in rep.get("channels", {}).items():
            rows.append((slot, ch, d))
            if d.get("alert"):
                alerts.append(f"{slot} {ch}：fallback {d['fallback_rate']:.0%}")
        for ch, ws in rep.get("warnings", {}).items():
            alerts += [f"{slot} {ch}：{w}" for w in ws]
    e = html.escape
    trs = "".join(
        f"<tr{' class=alert' if d.get('alert') else ''}><td>{e(s)}</td><td>{e(c)}</td><td>{d['cells']}</td>"
        f"<td>{d['changes']}</td><td>{d['ads']}</td><td>{d['low_conf']}</td><td>{d['fallback_rate']:.0%}</td></tr>"
        for s, c, d in rows)
    al = "".join(f"<li>{e(a)}</li>" for a in alerts) or "<li>無</li>"
    page = f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><title>監看日報 {e(day)}</title>
<style>body{{font:14px/1.5 system-ui,"PingFang TC",sans-serif;padding:16px;color:#1a1a1a;background:#fff}}
table{{border-collapse:collapse}}td,th{{border:1px solid #ccc;padding:3px 8px}}.alert{{background:#ffe0e0}}</style></head>
<body><h1>監看日報 {e(day)}</h1><h2>告警</h2><ul>{al}</ul><h2>各時段</h2>
<table><tr><th>時段</th><th>台別</th><th>格數</th><th>換題</th><th>廣告</th><th>低信心</th><th>fallback</th></tr>{trs}</table>
</body></html>"""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    return out


def send_report(path: Path, to: list[str], smtp_cfg: dict, smtp_factory=None) -> None:
    """Only called when rules.yaml configures daily_report.smtp; never by default."""
    import smtplib

    msg = EmailMessage()
    msg["Subject"] = f"監看日報 {path.stem}"
    msg["From"] = smtp_cfg["from"]
    msg["To"] = ", ".join(to)
    msg.set_content("附件為監看日報。")
    msg.add_alternative(path.read_text(encoding="utf-8"), subtype="html")
    factory = smtp_factory or smtplib.SMTP
    with factory(smtp_cfg["host"], smtp_cfg.get("port", 587)) as s:
        if smtp_cfg.get("starttls", True):
            s.starttls()
        if smtp_cfg.get("user"):
            s.login(smtp_cfg["user"], smtp_cfg["password"])
        s.send_message(msg)
