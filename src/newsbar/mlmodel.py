"""Logistic regression in numpy plus the on-disk model registry (default OFF; see README
「RAG 與 ML」). No scikit-learn: the fit is Newton/IRLS with an L2 penalty, features are
standardised, and a model is one JSON file (feature names, weights, threshold, window, metrics).

data/models/{kind}_v{N}.json   one file per trained version, never overwritten
data/models/active.json        {"change": "<file>", "ad": "<file>", "history": {"change": [...], "ad": [...]}}
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

DEFAULT_DIR = "data/models"


def _sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -35, 35)))


def fit_logreg(X: np.ndarray, y: np.ndarray, l2: float = 1.0, balanced: bool = True, iters: int = 50):
    """Weighted, L2-penalised logistic regression by Newton steps on standardised features.
    Returns (w, b, mean, std) with w in standardised space. The bias is not penalised."""
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    mean = X.mean(axis=0)
    std = X.std(axis=0)
    std[std < 1e-9] = 1.0
    Z = (X - mean) / std
    n, d = Z.shape
    sw = np.ones(n)
    if balanced and 0 < y.sum() < n:
        sw = np.where(y == 1, n / (2 * y.sum()), n / (2 * (n - y.sum())))
    A = np.hstack([Z, np.ones((n, 1))])
    pen = np.diag([l2] * d + [1e-8])
    beta = np.zeros(d + 1)
    for _ in range(iters):
        p = _sigmoid(A @ beta)
        grad = A.T @ (sw * (p - y)) + pen @ beta
        H = A.T @ (A * (sw * p * (1 - p))[:, None]) + pen
        step = np.linalg.solve(H + 1e-9 * np.eye(d + 1), grad)
        beta -= step
        if np.abs(step).max() < 1e-8:
            break
    return beta[:d], float(beta[d]), mean, std


def prf(y: np.ndarray, pred: np.ndarray) -> dict:
    tp = int(((y == 1) & (pred == 1)).sum())
    fp = int(((y == 0) & (pred == 1)).sum())
    fn = int(((y == 1) & (pred == 0)).sum())
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": round(p, 3), "recall": round(r, 3),
            "f1": round(2 * p * r / (p + r), 3) if p + r else 0.0}


def pick_threshold(p: np.ndarray, y: np.ndarray) -> float:
    """Threshold with the best F1 on the training rows; ties resolve to the middle of the tied
    range (a threshold hugging one training score would be the most overfitted choice)."""
    if len(set(y.tolist())) < 2:
        return 0.5
    u = np.unique(p)
    cands = np.concatenate([[u[0] - 1e-6], (u[:-1] + u[1:]) / 2, [u[-1] + 1e-6]])
    f1 = np.array([prf(y, (p >= t).astype(int))["f1"] for t in cands])
    best = np.flatnonzero(f1 >= f1.max() - 1e-12)
    return float(cands[best[len(best) // 2]])


@dataclass
class Model:
    kind: str
    features: list[str]
    weights: list[float]  # standardised space
    bias: float
    mean: list[float]
    std: list[float]
    threshold: float
    train_window: dict = field(default_factory=dict)
    metrics: dict = field(default_factory=dict)
    created_at: str = ""
    version: int = 0
    path: str = ""

    def predict_vec(self, x: np.ndarray) -> np.ndarray:
        z = (np.asarray(x, dtype=float) - np.array(self.mean)) / np.array(self.std)
        return _sigmoid(z @ np.array(self.weights) + self.bias)

    def predict(self, feats: dict) -> float:
        x = np.array([[float(feats.get(n, 0.0)) for n in self.features]])
        return float(self.predict_vec(x)[0])

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in ("kind", "version", "features", "weights", "bias", "mean", "std",
                                              "threshold", "train_window", "metrics", "created_at")}

    def weight_table(self) -> list[tuple[str, float]]:
        """Standardised weights, largest magnitude first (comparable across features)."""
        return sorted(zip(self.features, self.weights), key=lambda t: -abs(t[1]))


def train_model(kind: str, features: list[str], rows: list[dict], window: dict, l2: float = 1.0) -> Model:
    X = np.array([[r["features"][n] for n in features] for r in rows], dtype=float)
    y = np.array([r["label"] for r in rows], dtype=float)
    if y.sum() == 0 or y.sum() == len(y):
        raise ValueError(f"{kind}: 訓練資料只有一種標籤（正例 {int(y.sum())} / 共 {len(y)}），無法訓練")
    w, b, mean, std = fit_logreg(X, y, l2)
    m = Model(kind, list(features), [float(v) for v in w], b, [float(v) for v in mean], [float(v) for v in std],
              0.5, window, {}, time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    p = m.predict_vec(X)
    m.threshold = pick_threshold(p, y)
    m.metrics = {"train": {**prf(y.astype(int), (p >= m.threshold).astype(int)), "rows": len(y),
                           "positives": int(y.sum())}}
    return m


# ---- registry ----------------------------------------------------------------------------------
def _reg(dirpath: str | Path) -> Path:
    return Path(dirpath) / "active.json"


def read_registry(dirpath: str | Path = DEFAULT_DIR) -> dict:
    p = _reg(dirpath)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"history": {}}


def _versions(dirpath: Path, kind: str) -> list[int]:
    return sorted(int(m[1]) for f in dirpath.glob(f"{kind}_v*.json") if (m := re.fullmatch(rf"{kind}_v(\d+)\.json", f.name)))


def save_model(m: Model, dirpath: str | Path = DEFAULT_DIR) -> Path:
    d = Path(dirpath)
    d.mkdir(parents=True, exist_ok=True)
    m.version = (_versions(d, m.kind) or [0])[-1] + 1
    p = d / f"{m.kind}_v{m.version}.json"
    p.write_text(json.dumps(m.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
    m.path = str(p)
    return p


def set_active(kind: str, path: str | Path, dirpath: str | Path = DEFAULT_DIR) -> None:
    reg = read_registry(dirpath)
    hist = reg.setdefault("history", {}).setdefault(kind, [])
    if reg.get(kind) and (not hist or hist[-1] != reg[kind]):
        hist.append(reg[kind])
    reg[kind] = str(path)
    _reg(dirpath).parent.mkdir(parents=True, exist_ok=True)
    _reg(dirpath).write_text(json.dumps(reg, ensure_ascii=False, indent=1), encoding="utf-8")


def rollback(kind: str, dirpath: str | Path = DEFAULT_DIR) -> str | None:
    """Make the previously active model of `kind` active again; returns its path (None if there is none)."""
    reg = read_registry(dirpath)
    hist = reg.get("history", {}).get(kind, [])
    if not hist:
        return None
    prev = hist.pop()
    reg[kind] = prev
    _reg(dirpath).write_text(json.dumps(reg, ensure_ascii=False, indent=1), encoding="utf-8")
    return prev


_CACHE: dict[str, tuple[float, Model]] = {}


def load_model(path: str | Path, kind: str | None = None, dirpath: str | Path = DEFAULT_DIR) -> Model:
    """`path` is a model file, or the word "active" (the registry's current model of `kind`)."""
    p = Path(path)
    if str(path) == "active":
        if kind is None:
            raise ValueError("model: active needs a kind")
        cur = read_registry(dirpath).get(kind)
        if not cur:
            raise FileNotFoundError(f"沒有 active 的 {kind} 模型（{_reg(dirpath)}）")
        p = Path(cur)
    mt = p.stat().st_mtime
    hit = _CACHE.get(str(p))
    if hit and hit[0] == mt:
        return hit[1]
    d = json.loads(p.read_text(encoding="utf-8"))
    m = Model(**{k: d[k] for k in ("kind", "features", "weights", "bias", "mean", "std", "threshold")},
              train_window=d.get("train_window", {}), metrics=d.get("metrics", {}),
              created_at=d.get("created_at", ""), version=d.get("version", 0), path=str(p))
    _CACHE[str(p)] = (mt, m)
    return m
