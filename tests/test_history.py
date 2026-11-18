"""History search (NFR-27), rules rollback (FR-21), daily report (FR-18)."""

import json

from newsbar.history import daily_report, record_eval, rollback, search, send_report, should_rollback


def write_evidence(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")


def rec(ch, minute, value, bar):
    return {"channel": ch, "minute": minute, "value": value, "items": [{"type": "news", "bar_text": bar, "asr_text": None}],
            "signals": [], "confidence": "high", "fallback": None, "rules_version": "0.1"}


def test_search_by_keyword_channel_and_date(tmp_path):
    write_evidence(tmp_path / "a" / "12監看_20261106.evidence.jsonl",
                   [rec("tvbs", "1205", "土城翁輾亡", "新北土城七旬翁 遭貨車輾亡"), rec("cts", "1210", "高金遭起訴", "高金素梅共諜案起訴")])
    write_evidence(tmp_path / "b" / "12監看_20261107.evidence.jsonl", [rec("tvbs", "1201", "土城車禍續", "土城車禍 家屬發聲")])
    assert [r["date"] for r in search(tmp_path, "土城")] == ["20261106", "20261107"]
    assert len(search(tmp_path, "土城", date_from="20261107")) == 1
    assert [r["value"] for r in search(tmp_path, channel="cts")] == ["高金遭起訴"]
    assert search(tmp_path, "貨車")[0]["minute"] == "1205"  # matches bar text, not just the label


def test_rollback_after_two_bad_weeks(tmp_path):
    h = tmp_path / "eval_history.jsonl"
    record_eval(h, "0.2", 0.70, 0.9, "20261102")  # ISO week 44
    assert not should_rollback(h, "0.2")
    record_eval(h, "0.2", 0.72, 0.9, "20261110")  # week 46
    assert should_rollback(h, "0.2")
    record_eval(h, "0.2", 0.95, 0.9, "20261111")  # week 46 average now ≥0.75
    assert not should_rollback(h, "0.2")

    rd = tmp_path / "rules"
    rd.mkdir()
    for v in ("0.1", "0.2", "0.10"):
        (rd / f"v{v}.yaml").write_text(f"version: {v}\n")
    active = tmp_path / "rules.yaml"
    assert rollback(rd, active, "0.10", tmp_path / "rollback.log") == "0.2"  # numeric order, not string order
    assert active.read_text() == "version: 0.2\n"
    assert rollback(rd, active, "0.1", tmp_path / "rollback.log") is None


def test_daily_report_and_sender(tmp_path):
    rep = {"channels": {"tvbs": {"cells": 20, "changes": 18, "ads": 2, "low_conf": 5, "fallback": 6,
                                 "fallback_rate": 0.3, "alert": True}}, "warnings": {"cts": ["處理失敗：x"]}}
    p = tmp_path / "12監看_20261106.report.json"
    p.write_text(json.dumps(rep), encoding="utf-8")
    page = daily_report([p], tmp_path / "daily.html", "20261106").read_text(encoding="utf-8")
    assert "fallback 30%" in page and "處理失敗" in page and 'class=alert' in page

    sent = {}

    class FakeSMTP:
        def __init__(self, host, port):
            sent["host"] = host

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            sent["tls"] = True

        def login(self, u, p):
            sent["user"] = u

        def send_message(self, m):
            sent["to"] = m["To"]

    send_report(tmp_path / "daily.html", ["ops@example.com"], {"host": "smtp.local", "from": "bot@example.com"}, FakeSMTP)
    assert sent == {"host": "smtp.local", "tls": True, "to": "ops@example.com"}
