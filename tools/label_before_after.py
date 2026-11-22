"""98-cell before/after table (規格 §3): docs/review/label_before_after.csv.

old = out/baseline_1801 (18:01), new = a Stage A replay. Old warnings = what the old review copy marked
yellow (excel_writer.flag_reasons on the old record: reasons, fallback codes, plain low confidence).
New warnings = the 建議回查 / 處理失敗 items only (資訊提示 is listed separately and is not yellow).
Human judgments come from docs/review/old_label_judgments.json and new_label_judgments.json.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

FB_TEXT = {"FB-1": "換題訊號偏弱", "FB-3": "標題讀不到，靠語音定名", "FB-4": "AI 五字標未通過校驗，改用規則擷取"}


def old_flags(rec: dict) -> list[str]:
    out = list(rec.get("reasons") or [])
    for code in filter(None, (rec.get("fallback") or "").split(",")):
        out.append(FB_TEXT.get(code, f"備援 {code}"))
    if not out and rec.get("confidence") == "low":
        out.append("信心偏低")
    return list(dict.fromkeys(out))


def new_flags(rec: dict) -> tuple[list[str], list[str]]:
    items = rec.get("review_items") or []
    flag = [n["reason"] + (f"：{n['detail']}" if n.get("detail") else "") for n in items
            if n["level"] in ("建議回查", "處理失敗")]
    info = [n["reason"] for n in items if n["level"] == "資訊提示"]
    return list(dict.fromkeys(flag)), list(dict.fromkeys(info))


def main(old_path: str, new_path: str, out_csv: str) -> int:
    old = {(r["channel"], r["minute"]): r for r in map(json.loads, open(old_path, encoding="utf-8"))}
    new = {(r["channel"], r["minute"]): r for r in map(json.loads, open(new_path, encoding="utf-8"))}
    jo = json.loads((ROOT / "docs/review/old_label_judgments.json").read_text(encoding="utf-8"))
    jn_path = ROOT / "docs/review/new_label_judgments.json"
    jn = json.loads(jn_path.read_text(encoding="utf-8")) if jn_path.exists() else {}
    rows = []
    for key in sorted(old):
        o = old[key]
        if not o.get("value"):
            continue
        n = new.get(key) or {}
        k = f"{key[0]}{key[1]}"
        is_ad = all(i["type"] != "news" for i in o["items"])
        of = old_flags(o)
        nf, ninfo = new_flags(n) if n else ([], [])
        if is_ad:
            old_bad, old_why = False, "廣告格（格式固定）"
        else:
            old_bad, old_why = jo.get(k, [None, "未判定"])
        if n.get("value") == o["value"]:
            new_bad, new_why = old_bad, "與舊版相同：" + old_why
        else:
            new_bad, new_why = jn.get(k, [None, "未判定"]) if not is_ad else (False, "廣告格")
        fixed = bool(old_bad) and new_bad is False
        let_through = bool(new_bad) and not nf
        rows.append({
            "channel": key[0], "minute": key[1],
            "舊短標": o["value"], "新短標": n.get("value", "（新版此格空白）"),
            "舊警告": "；".join(of), "新警告": "；".join(nf), "新資訊提示（不標黃）": "；".join(ninfo),
            "舊版人工判定": ("有問題" if old_bad else "無問題") if old_bad is not None else "未判定",
            "新版人工判定": ("有問題" if new_bad else "無問題") if new_bad is not None else "未判定",
            "判定理由": f"舊：{old_why}｜新：{new_why}",
            "是否修正": "是" if fixed else ("否" if old_bad else "—"),
            "是否有錯誤被新版放行": "是" if let_through else "否",
            "舊版有警告": bool(of), "新版有警告": bool(nf),
        })
    Path(out_csv).parent.mkdir(parents=True, exist_ok=True)
    with open(out_csv, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    s = {
        "cells": len(rows), "ad_cells": sum(r["判定理由"].startswith("舊：廣告") for r in rows),
        "old_warned": sum(r["舊版有警告"] for r in rows), "new_warned": sum(r["新版有警告"] for r in rows),
        "old_problems": sum(r["舊版人工判定"] == "有問題" for r in rows),
        "new_problems": sum(r["新版人工判定"] == "有問題" for r in rows),
        "fixed": sum(r["是否修正"] == "是" for r in rows),
        "let_through": sum(r["是否有錯誤被新版放行"] == "是" for r in rows),
        "unnecessary_old_warnings_removed": sum(r["舊版有警告"] and not r["新版有警告"] and r["新版人工判定"] == "無問題"
                                                for r in rows),
        "old_problems_without_warning": sum(r["舊版人工判定"] == "有問題" and not r["舊版有警告"] for r in rows),
        "undetermined": sum("未判定" in (r["舊版人工判定"], r["新版人工判定"]) for r in rows),
    }
    print(json.dumps(s, ensure_ascii=False))
    Path(out_csv).with_suffix(".summary.json").write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:4]))
