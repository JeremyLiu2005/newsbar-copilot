"""`newsbar serve`: the operator page drives the real pipeline through a small JSON API."""

import json
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest

from newsbar import server

FAKE_RUN = r"""
import json, sys, pathlib
out = pathlib.Path(sys.argv[sys.argv.index('--out') + 1])
print('[tvbs.ts] 畫面時鐘校時 → 第 0 秒 = 11:59:46', flush=True)
print('[tvbs] OCR …', flush=True)
print('[tvbs] 語音轉錄 …', flush=True)
out.write_bytes(b'PK-fake-xlsx')
out.with_suffix('.evidence.jsonl').write_text(json.dumps({'channel': 'tvbs', 'minute': '1200', 'value': '土城翁輾亡',
    'confidence': 'high', 'fallback': None, 'signals': ['NEW-1'],
    'items': [{'type': 'news', 'label': '土城翁輾亡', 'start_sec': 5, 'bar_text': '新北土城一七旬老翁',
    'asr_text': '新北土城一名七旬老翁', 'frame': 'data/work/tvbs/frames/main_bar/00019.jpg'}]}, ensure_ascii=False) + '\n')
out.with_suffix('.report.json').write_text(json.dumps({'channels': {'tvbs': {'cells': 1, 'low_conf': 0}}}))
print('tvbs: 1 格，換題 1，廣告 0，低信心 0', flush=True)
"""


@pytest.fixture
def app(tmp_path):
    vids = tmp_path / "raw"
    vids.mkdir()
    (vids / "tvbs.ts").write_bytes(b"x")
    (vids / "template.xlsx").write_bytes(b"x")
    bar = tmp_path / "work" / "tvbs" / "frames" / "main_bar"
    bar.mkdir(parents=True)
    (bar / "00019.jpg").write_bytes(b"JPEGBAR")
    script = tmp_path / "fake_run.py"
    script.write_text(FAKE_RUN)
    s = server.Settings(video_dir=vids, template=vids / "template.xlsx", out_dir=tmp_path / "out",
                        channels=tmp_path / "c.yaml", rules=tmp_path / "r.yaml", rules_fast=None,
                        workdir=tmp_path / "work")
    jobs = server.Jobs(launch=lambda cmd: __import__("subprocess").Popen(
        [sys.executable, str(script), *cmd[3:]], stdout=-1, stderr=-2, text=True))
    httpd = server.serve(s, port=0, jobs=jobs)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def get(url):
    with urllib.request.urlopen(url) as r:
        return r.status, r.headers.get("Content-Type"), r.read()


def post(url, body):
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_page_and_inputs(app):
    code, ctype, body = get(app + "/")
    assert code == 200 and "text/html" in ctype and "開始分析".encode() in body
    inp = json.loads(get(app + "/api/inputs")[2])
    assert inp["guess"]["tvbs"] == "tvbs.ts" and inp["template"] == "template.xlsx"


def test_job_runs_and_serves_result_and_xlsx(app):
    code, job = post(app + "/api/jobs", {"videos": {"tvbs": "tvbs.ts"}})
    assert code == 201
    for _ in range(50):
        j = json.loads(get(f"{app}/api/jobs/{job['id']}")[2])
        if j["state"] != "running":
            break
        time.sleep(0.1)
    assert j["state"] == "done" and j["stages"]["tvbs"] == "完成"
    res = json.loads(get(f"{app}/api/jobs/{job['id']}/result")[2])
    assert res["cells"][0]["v"] == "土城翁輾亡" and res["report"]["tvbs"]["cells"] == 1
    code, ctype, body = get(f"{app}/api/jobs/{job['id']}/xlsx")
    assert body == b"PK-fake-xlsx" and "spreadsheetml" in ctype
    item = res["cells"][0]["items"][0]
    assert item["asr"] == "新北土城一名七旬老翁" and item["idx"] == 19
    code, ctype, body = get(f"{app}/api/jobs/{job['id']}/bar/tvbs/19")
    assert body == b"JPEGBAR" and ctype == "image/jpeg"
    try:
        get(f"{app}/api/jobs/{job['id']}/bar/ftv/19")
        raise AssertionError("expected 404")
    except urllib.error.HTTPError as e:
        assert e.code == 404


def test_rejects_files_outside_the_video_folder(app):
    code, err = post(app + "/api/jobs", {"videos": {"tvbs": "../fake_run.py"}})
    assert code == 400 and "找不到" in err["error"]
    code, err = post(app + "/api/jobs", {"videos": {}})
    assert code == 400


def test_stage_parsing():
    j = server.Job("abcd1234", [], Path("x"))
    for line in ["[ftv.ts] 畫面時鐘校時 → 第 0 秒 = 11:59:46", "[ftv] 語音轉錄 …", "[2026] ctranslate2 warning"]:
        j.feed(line)
    assert j.stages["ftv"] == "語音轉錄"
    j.feed("ftv: 20 格，換題 18")
    assert j.stages["ftv"] == "完成"


def test_screenshot_second_skips_blank_switch_over():
    rows = [(5, 19, ""), (6, 20, "新北土城一七旬老翁"), (7, 21, "新北土城一七旬老翁"), (8, 22, "新北土城一七旬老翁")]
    item = {"type": "news", "start_sec": 5, "bar_text": "新北土城一七旬老翁", "frame": "x/00019.jpg"}
    assert server.shot_index(rows, item, 100) == 21
    assert server.shot_index([], item, 100) == 19


def test_ad_break_gets_four_pictures_across_it():
    rows = [(s, s + 14, "") for s in range(100, 200)]
    strip = server.ad_strip(rows, 100, 200)
    assert len(strip) == 4 and strip[0][0] == 105 and strip[-1][0] == 195


def test_manual_review_saves_and_replaces_corrections(app):
    _, job = post(app + "/api/jobs", {"videos": {"tvbs": "tvbs.ts"}})
    for _ in range(50):
        if json.loads(get(f"{app}/api/jobs/{job['id']}")[2])["state"] != "running":
            break
        time.sleep(0.1)
    url = f"{app}/api/jobs/{job['id']}/corrections"
    code, err = post(url, {"channel": "tvbs", "minute": "1200", "verdict": "fix", "correct": "土城翁輾亡"})
    assert code == 400 and "審核者" in err["error"]
    code, err = post(url, {"channel": "tvbs", "minute": "1200", "verdict": "fix", "correct": "x", "reviewer": "A"})
    assert code == 400 and "錯誤類型" in err["error"]
    code, r = post(url, {"channel": "tvbs", "minute": "1200", "verdict": "fix", "correct": "",
                         "error_type": "包框誤判", "reviewer": "A", "original": "廣告"})
    assert code == 200 and r["items"][0]["correct"] == "" and r["items"][0]["original"] == "廣告"
    code, r = post(url, {"channel": "tvbs", "minute": "1200", "verdict": "ok", "reviewer": "B"})
    assert len(r["items"]) == 1 and r["items"][0]["verdict"] == "ok" and r["items"][0]["reviewer"] == "B"
    code, r = post(url, {"channel": "tvbs", "minute": "1200", "verdict": "remove", "reviewer": "B"})
    assert r["items"] == []
    code, err = post(url, {"channel": "cts", "minute": "1200", "verdict": "ok", "reviewer": "B"})
    assert code == 400
