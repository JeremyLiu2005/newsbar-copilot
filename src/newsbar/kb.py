"""Label knowledge base for retrieval-augmented 五字標 writing (RAG; default OFF).

One record = a (title, label) pair a human has seen and accepted:
    {id, title, label, channel, date, source: gold | correction | history | seed}
Pairs come from the gold file, from reviewers' corrections (fix → the corrected label, ok → the
label that was kept), from a historical 監看表 whose titles are known, and from the few-shot seed.
Only pairs whose label passes labeler.validate() against their own title are kept, so a retrieved
example never teaches the model to break the rules the validator enforces.

Retrieval is character-bigram TF-IDF cosine in numpy (no embedding model, no new dependency).
"""

from __future__ import annotations

import csv
import json
import math
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from . import labeler

DEFAULT_KB = "data/kb/labels.jsonl"
SOURCES = ("gold", "correction", "history", "seed")
_KEEP = re.compile(r"[^0-9A-Za-z一-鿿]+")


@dataclass
class Record:
    id: str
    title: str
    label: str
    channel: str = ""
    date: str = ""
    source: str = "seed"

    def to_dict(self) -> dict:
        return asdict(self)


def pair_errors(title: str, label: str) -> list[str]:
    """Rule ids the label breaks against its own title ([] = usable)."""
    if not title or not label:
        return ["EMPTY"]
    return labeler.validate(label, labeler.LabelSource([title]))


def _norm(s: str) -> str:
    return _KEEP.sub("", s).lower()


def grams(s: str) -> list[str]:
    t = _norm(s)
    if len(t) < 2:
        return [t] if t else []
    return [t[i:i + 2] for i in range(len(t) - 1)]


class KB:
    """In-memory KB with a bigram TF-IDF inverted index (rebuilt lazily after changes)."""

    def __init__(self, records: list[Record] | None = None):
        self.records: list[Record] = list(records or [])
        self._idx = None

    # ---- storage -------------------------------------------------------------------------
    @classmethod
    def load(cls, path: str | Path) -> "KB":
        p = Path(path)
        recs: list[Record] = []
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    d = json.loads(line)
                    recs.append(Record(**{k: d.get(k, "") for k in Record.__dataclass_fields__}))
        return cls(recs)

    def save(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text("".join(json.dumps(r.to_dict(), ensure_ascii=False) + "\n" for r in self.records),
                       encoding="utf-8")
        tmp.replace(p)

    def upsert(self, rec: Record) -> bool:
        """Add or refresh by id. Returns False (and stores nothing) when the pair fails validate."""
        if pair_errors(rec.title, rec.label):
            return False
        for i, r in enumerate(self.records):
            if r.id == rec.id:
                self.records[i] = rec
                break
        else:
            self.records.append(rec)
        self._idx = None
        return True

    def remove(self, rid: str) -> None:
        self.records = [r for r in self.records if r.id != rid]
        self._idx = None

    # ---- retrieval -----------------------------------------------------------------------
    def _index(self):
        if self._idx is not None:
            return self._idx
        docs = [Counter(grams(r.title)) for r in self.records]
        n = len(docs)
        df: Counter = Counter()
        for d in docs:
            df.update(d.keys())
        idf = {g: math.log((1 + n) / (1 + c)) + 1.0 for g, c in df.items()}
        post: dict[str, tuple[list[int], list[float]]] = {}
        for i, d in enumerate(docs):
            w = {g: (1 + math.log(c)) * idf[g] for g, c in d.items()}
            norm = math.sqrt(sum(x * x for x in w.values())) or 1.0
            for g, x in w.items():
                ids, ws = post.setdefault(g, ([], []))
                ids.append(i)
                ws.append(x / norm)
        self._idx = (idf, {g: (np.array(a), np.array(b)) for g, (a, b) in post.items()})
        return self._idx

    def scores(self, title: str) -> np.ndarray:
        idf, post = self._index()
        out = np.zeros(len(self.records))
        q = Counter(g for g in grams(title) if g in idf)
        if not q:
            return out
        w = {g: (1 + math.log(c)) * idf[g] for g, c in q.items()}
        norm = math.sqrt(sum(x * x for x in w.values())) or 1.0
        for g, x in w.items():
            ids, ws = post[g]
            np.add.at(out, ids, ws * (x / norm))
        return out

    def retrieve(self, title: str, k: int = 3, exclude_ids=(), exclude_labels=(), skip_pairs=()) -> list[Record]:
        """Top-k most similar stored titles (cosine > 0). `exclude_ids` drops exactly those
        records (leave-one-out); `exclude_labels` drops every record carrying such a label;
        `skip_pairs` drops (title, label) pairs the prompt already contains."""
        if not self.records or k <= 0:
            return []
        sc = self.scores(title)
        order = sorted(range(len(sc)), key=lambda i: (-sc[i], self.records[i].id))
        ex_ids, ex_lab, skip = set(exclude_ids), set(exclude_labels), set(skip_pairs)
        out: list[Record] = []
        seen: set[tuple[str, str]] = set()
        for i in order:
            if sc[i] <= 1e-9 or len(out) >= k:
                break
            r = self.records[i]
            key = (r.title, r.label)
            if r.id in ex_ids or r.label in ex_lab or key in skip or key in seen:
                continue
            seen.add(key)
            out.append(r)
        return out


@dataclass
class Rag:
    """What a generator needs: a KB and how many pairs to show."""
    kb: KB
    k: int = 3

    def retrieve(self, title: str, exclude_ids=(), exclude_labels=(), skip_pairs=()) -> list[Record]:
        return self.kb.retrieve(title, self.k, exclude_ids, exclude_labels, skip_pairs)


def rag_from_rules(rules: dict | None, kb_override: str | None = None) -> Rag | None:
    """labeler.rag {enabled: false, k: 3, kb: data/kb/labels.jsonl}. `kb_override` (CLI --kb) names a
    KB file and switches RAG on. Returns None when RAG is off (prompts stay byte-identical to v3)."""
    cfg = ((rules or {}).get("labeler") or {}).get("rag") or {}
    if not (cfg.get("enabled") or kb_override):
        return None
    return Rag(KB.load(kb_override or cfg.get("kb") or DEFAULT_KB), int(cfg.get("k", 3)))


# ---- collecting ------------------------------------------------------------------------------
def _stable_titles(obs, min_sec: int = 7) -> list[tuple[int, str]]:
    """(start_sec, text) of every bar reading that lasted ≥ min_sec — TIME-1's notion of a title."""
    from .segmenter import is_credit, is_weather

    out, cur, start, n = [], None, 0, 0
    for o in sorted(obs, key=lambda o: o.sec):
        t = o.bar_text
        if t == cur:
            n += 1
            continue
        if cur and n >= min_sec and not is_credit(cur) and not is_weather(cur):
            out.append((start, cur))
        cur, start, n = t, o.sec, 1
    if cur and n >= min_sec and not is_credit(cur) and not is_weather(cur):
        out.append((start, cur))
    return out


def collect_gold(gold_path: Path, work_root: Path, date: str, window: int = 10) -> tuple[list[Record], list[str]]:
    """Gold news items → (title, label). The title is the nearest stable bar reading within ±window s
    of the gold second in work_root/<channel>/obs.jsonl."""
    from . import extract

    gold = json.loads(Path(gold_path).read_text(encoding="utf-8"))
    obs_cache: dict[str, list] = {}
    recs: list[Record] = []
    notes: list[str] = []
    for it in gold.get("items", []):
        if it.get("kind") != "news" or not it.get("label"):
            continue
        ch, sec = it["channel"], int(it["sec"])
        if ch not in obs_cache:
            p = Path(work_root) / ch / "obs.jsonl"
            obs_cache[ch] = _stable_titles(extract.load_obs(p)) if p.exists() else []
        near = [(abs(s - sec), s, t) for s, t in obs_cache[ch] if abs(s - sec) <= window]
        if not near:
            notes.append(f"gold {ch}@{sec}s {it['label']}: no stable title within ±{window}s")
            continue
        _, _, title = min(near)
        recs.append(Record(f"gold:{ch}:{sec}", title, it["label"], ch, date, "gold"))
    return recs, notes


def _evidence_for(dirpath: Path) -> dict[tuple[str, str], dict]:
    out: dict[tuple[str, str], dict] = {}
    for p in sorted(dirpath.glob("*.evidence.jsonl")):
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                out[(r["channel"], r["minute"])] = r
    return out


def records_from_correction(row: dict, evidence: dict | None, job_id: str = "") -> list[Record]:
    """One correction → 0..n records. fix: the corrected items, paired in order with the cell's
    news items; ok: the kept items. Cells whose items cannot be paired with a title are skipped."""
    if not evidence:
        return []
    news = [it for it in evidence.get("items", []) if it.get("type") == "news" and it.get("bar_text")]
    ch, minute = row["channel"], row["minute"]
    date = str(row.get("reviewed_at") or "")[:10]
    if row.get("verdict") == "ok":
        labels = [it["label"] for it in news]
    elif row.get("verdict") == "fix" and row.get("correct"):
        labels = [x.strip() for x in str(row["correct"]).split("/") if x.strip()]
        labels = [x for x in labels if x not in ("廣告", "氣象", "訊號中斷")]
    else:
        return []
    by_label = {it["label"]: it for it in news}
    out = []
    if len(labels) == len(news):  # same number of items → pair in time order
        pairs = list(zip(labels, news))
    else:  # otherwise only labels that match an item exactly
        pairs = [(lab, by_label[lab]) for lab in labels if lab in by_label]
    for n, (lab, it) in enumerate(pairs):
        out.append(Record(f"corr:{job_id}:{ch}:{minute}:{n}", it["bar_text"], lab, ch, date, "correction"))
    return out


def collect_corrections(out_root: Path) -> tuple[list[Record], list[str]]:
    recs: list[Record] = []
    notes: list[str] = []
    for cf in sorted(Path(out_root).glob("*/corrections.json")):
        rows = json.loads(cf.read_text(encoding="utf-8"))
        ev = _evidence_for(cf.parent)
        for row in rows:
            got = records_from_correction(row, ev.get((row["channel"], row["minute"])), cf.parent.name)
            if not got:
                notes.append(f"{cf.parent.name} {row['channel']}@{row['minute']}: no title pair")
            recs += got
    return recs, notes


def collect_seed() -> list[Record]:
    from .llm import FEW_SHOT

    return [Record(f"seed:{i}", q, a, "", "", "seed") for i, (q, a) in enumerate(FEW_SHOT)]


def build(gold_path: Path | None, work_root: Path, out_root: Path, date: str = "2026-11-06",
          seed: bool = True, keep_sources: tuple[str, ...] = ("history",), existing: KB | None = None) -> tuple[KB, dict]:
    """Rebuild the KB from gold + corrections + seed. Records of the `keep_sources` already in
    `existing` (history imports have no machine-readable origin to re-derive) are carried over."""
    cand: list[Record] = []
    notes: list[str] = []
    if gold_path and Path(gold_path).exists():
        r, n = collect_gold(gold_path, work_root, date)
        cand += r
        notes += n
    if Path(out_root).exists():
        r, n = collect_corrections(out_root)
        cand += r
        notes += n
    if seed:
        cand += collect_seed()
    if existing:
        cand += [r for r in existing.records if r.source in keep_sources]
    kb = KB()
    stats: dict = {"candidates": Counter(r.source for r in cand), "kept": Counter(), "rejected": []}
    for r in cand:
        errs = pair_errors(r.title, r.label)
        if errs:
            stats["rejected"].append({"id": r.id, "title": r.title, "label": r.label, "errors": errs})
        elif any(x.id == r.id for x in kb.records):
            continue
        else:
            kb.records.append(r)
            stats["kept"][r.source] += 1
    stats["notes"] = notes
    return kb, stats


# ---- historical sheets -----------------------------------------------------------------------
def _read_titles(path: Path) -> dict[tuple[str, str], list[str]]:
    """Titles per (channel, minute): a CSV/JSON/JSONL with {channel, minute, title}, or an
    *.evidence.jsonl written by `newsbar run` (items[].bar_text of the news items)."""
    out: dict[tuple[str, str], list[str]] = {}
    p = Path(path)
    if p.suffix == ".csv":
        rows = list(csv.DictReader(p.open(encoding="utf-8")))
    elif p.suffix == ".json":
        rows = json.loads(p.read_text(encoding="utf-8"))
    else:
        rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    for r in rows:
        if "items" in r:  # evidence record
            ts = [it["bar_text"] for it in r["items"] if it.get("type") == "news" and it.get("bar_text")]
        elif r.get("title"):
            ts = [r["title"]]
        else:
            ts = []
        if ts:
            out.setdefault((r["channel"], str(r["minute"])), []).extend(ts)
    return out


def add_sheet(kb: KB, sheet: Path, titles: Path | None, channel_headers: dict[str, str], date: str) -> dict:
    """Import a historical 監看表. A sheet holds only labels; a label can be paired with a title only
    when the titles are supplied separately (`titles`: see _read_titles). Without them nothing is
    added — a label alone teaches nothing about how to read a title."""
    from . import evaluate

    if titles is None or not Path(titles).exists():
        return {"added": 0, "skipped": 0, "error": "沒有標題檔：只有五字標的舊表無法配成（標題, 五字標）配對"}
    sheet_cells = evaluate.read_sheet(Path(sheet), channel_headers)
    tmap = _read_titles(Path(titles))
    added = skipped = 0
    for ch, col in sheet_cells.items():
        for minute, value in col.items():
            labels = [x for x in evaluate.items(value) if x not in (evaluate.AD, evaluate.OUTAGE)]
            ts = tmap.get((ch, minute), [])
            if len(labels) != len(ts):
                skipped += len(labels)
                continue
            for n, (lab, ti) in enumerate(zip(labels, ts)):
                ok = kb.upsert(Record(f"hist:{date}:{ch}:{minute}:{n}", ti, lab, ch, date, "history"))
                added += ok
                skipped += not ok
    return {"added": added, "skipped": skipped}


# ---- server feedback -------------------------------------------------------------------------
def feed_correction(kb_path: Path, row: dict, evidence_dir: Path, job_id: str) -> int:
    """Append/refresh the KB entries for one saved correction. A 'remove' verdict deletes them."""
    kb = KB.load(kb_path)
    ev = _evidence_for(evidence_dir).get((row["channel"], row["minute"]))
    prefix = f"corr:{job_id}:{row['channel']}:{row['minute']}:"
    old = [r for r in kb.records if r.id.startswith(prefix)]
    for r in old:
        kb.remove(r.id)
    n = 0
    if row.get("verdict") in ("ok", "fix"):
        for rec in records_from_correction(row, ev, job_id):
            n += kb.upsert(rec)
    if n or old:  # nothing to learn and nothing to forget: do not create the file
        kb.save(kb_path)
    return n


# ---- evaluation ------------------------------------------------------------------------------
def char_f1(pred: str, gold: str) -> float:
    """Character-overlap F1 (multiset)."""
    p, g = Counter(pred), Counter(gold)
    inter = sum((p & g).values())
    if not inter:
        return 0.0
    pr, rc = inter / sum(p.values()), inter / sum(g.values())
    return 2 * pr * rc / (pr + rc)


@dataclass
class EvalRow:
    id: str
    title: str
    gold: str
    label: str
    first_ok: bool
    fallback: bool
    f1: float
    retrieved: list[str] = field(default_factory=list)


def eval_items(gold_path: Path, work_root: Path) -> list[Record]:
    recs, _ = collect_gold(gold_path, work_root, "")
    return recs


def run_eval(items: list[Record], kb: KB, make_gen, k: int, mode: str) -> list[EvalRow]:
    """mode: off | loo (leave the item's own record out) | strict (also drop every record carrying
    the same label — the same story coming back would otherwise hand over the answer).
    `make_gen(rag, exclude_ids, exclude_labels)` returns a Generator."""
    rows = []
    for it in items:
        if mode == "off":
            gen = make_gen(None, (), ())
        else:
            ex_l = (it.label,) if mode == "strict" else ()
            gen = make_gen(Rag(kb, k), (it.id,), ex_l)
        src = labeler.LabelSource([it.title])
        res = labeler.make_label(src, gen, 1)
        retrieved = []
        if mode != "off":
            retrieved = [r.id for r in gen.rag_pairs(src)] if hasattr(gen, "rag_pairs") else []
        rows.append(EvalRow(it.id, it.title, it.label, res.label, bool(res.errors) and not res.errors[0],
                            res.fallback is not None, char_f1(res.label, it.label), retrieved))
    return rows


def summarize(rows: list[EvalRow]) -> dict:
    n = max(1, len(rows))
    return {"n": len(rows), "char_f1": round(sum(r.f1 for r in rows) / n, 3),
            "first_attempt_valid": round(sum(r.first_ok for r in rows) / n, 3),
            "fallback_to_rule": sum(r.fallback for r in rows)}


# ---- CLI handlers ----------------------------------------------------------------------------
def _rules(path) -> dict:
    import yaml

    p = Path(path) if path else None
    return (yaml.safe_load(p.read_text(encoding="utf-8")) or {}) if p and p.exists() else {}


def cmd_build(a) -> int:
    existing = KB.load(a.kb) if Path(a.kb).exists() else None
    kb, st = build(Path(a.gold) if a.gold else None, Path(a.workdir), Path(a.out_root), a.date, not a.no_seed,
                   existing=existing)
    kb.save(a.kb)
    print(f"候選 {dict(st['candidates'])} → 收進 {dict(st['kept'])}，共 {len(kb.records)} 筆 → {a.kb}")
    for r in st["rejected"]:
        print(f"  剔除（validate {','.join(r['errors'])}）：{r['title']} → {r['label']}")
    for n in st["notes"]:
        print(f"  略過：{n}")
    return 0


def cmd_add_sheet(a) -> int:
    import yaml

    cfg = yaml.safe_load(Path(a.channels).read_text(encoding="utf-8"))["channels"]
    headers = {k: v["excel_column_header"] for k, v in cfg.items()}
    kb = KB.load(a.kb)
    res = add_sheet(kb, Path(a.sheet), Path(a.titles) if a.titles else None, headers, a.date)
    if res.get("error"):
        print(res["error"])
        return 1
    kb.save(a.kb)
    print(f"新增 {res['added']} 筆，略過 {res['skipped']}（標題數與五字標數對不上，或未通過 validate）→ {a.kb}")
    return 0


def _pick_host(rules: dict) -> str | None:
    from .llm import ollama_up

    lab = rules.get("labeler") or {}
    for h in dict.fromkeys([lab.get("host"), "http://127.0.0.1:11435", "http://127.0.0.1:11434"]):
        if h and ollama_up(h):
            return h
    return None


def _has_model(host: str, model: str) -> bool:
    import urllib.request

    try:
        with urllib.request.urlopen(f"{host.rstrip('/')}/api/tags", timeout=3) as r:
            return any(m.get("name") == model for m in json.loads(r.read().decode()).get("models", []))
    except (OSError, ValueError):
        return False


def retrieval_diagnostics(items: list[Record], kb: KB, k: int) -> dict:
    """No-LLM check of what RAG would put in the prompt (strict leave-one-out: the item's own record
    and every record with the same label are excluded). NOT a label-quality result."""
    from .llm import FEW_SHOT

    hit = best = 0.0
    for it in items:
        got = kb.retrieve(it.title, k, (it.id,), (it.label,), set(FEW_SHOT))
        hit += bool(got)
        best += max((char_f1(r.label, it.label) for r in got), default=0.0)
    n = max(1, len(items))
    return {"items": len(items), "with_any_retrieved": int(hit), "mean_best_retrieved_label_charF1": round(best / n, 3)}


def cmd_eval(a) -> int:
    from .llm import OllamaGenerator

    rules = _rules(a.rules)
    items = eval_items(Path(a.gold), Path(a.workdir))
    kb = KB.load(a.kb)
    print(f"評估題數 {len(items)}（TVBS gold，標題取自 obs.jsonl）；KB {len(kb.records)} 筆 "
          f"{dict(Counter(r.source for r in kb.records))}")
    print("檢索診斷（不經 LLM）：", retrieval_diagnostics(items, kb, a.k))
    host = _pick_host(rules)
    model = (rules.get("labeler") or {}).get("model", "qwen2.5:7b-instruct")
    if host is None:
        print("Ollama 無法連線（已試規則裡的 host、11435、11434）：無法產出 RAG 對照數字。"
              "請先啟動 ollama serve 並確認 qwen2.5:7b-instruct 已安裝後重跑。")
        return 0
    if not _has_model(host, model):
        print(f"Ollama 在線（{host}）但沒有安裝 {model}：無法產出 RAG 對照數字（不會自動下載）。"
              f"請先 ollama pull {model} 後重跑。")
        return 0
    print(f"Ollama {host} / {model}，k={a.k}")

    def make(rag, ids, labs):
        return OllamaGenerator(model=model, host=host, rag=rag, rag_exclude_ids=tuple(ids), rag_exclude_labels=tuple(labs))

    out = {}
    for mode in ("off", "loo", "strict"):
        rows = run_eval(items, kb, make, a.k, mode)
        out[mode] = (rows, summarize(rows))
        print(f"  {mode:6s} {out[mode][1]}")
    if a.detail:
        for off, on in zip(out["off"][0], out["strict"][0]):
            print(f"  {off.gold:8s} off:{off.label:8s}({off.f1:.2f}) strict:{on.label:8s}({on.f1:.2f})  ← {on.retrieved}")
    return 0
