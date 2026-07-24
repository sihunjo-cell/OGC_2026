#!/usr/bin/env python
"""Train a small per-instance solver-arm selector.

The model is intentionally simple and dependency-light:

* Input: feature rows from `features.jsonl`.
* Target: per-arm log(objective) from `runs.jsonl`.
* Model: one ridge-regression head per arm.
* Decision: choose the arm with the lowest predicted objective.

This gives a useful first test: whether cheap instance features can recover a
meaningful fraction of the virtual-best-solver gap.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

import numpy as np

SCHEMA = "ogc-ml-selector-v1"
DATA_SCHEMA = "ogc-ml-config-v1"
NON_FEATURE_KEYS = {
    "schema",
    "name",
    "prob",
    "prob_hash",
    "source_path",
    "created_at",
}


def _read_jsonl(path: Path):
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("schema") == DATA_SCHEMA:
                rows.append(rec)
    return rows


def _latest_by(rows, key_fn):
    out = {}
    for r in rows:
        out[key_fn(r)] = r
    return out


def _feature_names(feature_rows):
    keys = set()
    for row in feature_rows:
        for k, v in row.items():
            if k in NON_FEATURE_KEYS:
                continue
            if isinstance(v, (int, float)) and math.isfinite(float(v)):
                keys.add(k)
    return sorted(keys)


def _build_matrix(features_path: Path, runs_path: Path, arm_set: str, timelimit: float):
    features = _latest_by(_read_jsonl(features_path), lambda r: r["prob_hash"])
    runs = _read_jsonl(runs_path)
    grouped = {}
    for r in runs:
        if r.get("arm_set") != arm_set:
            continue
        if float(r.get("timelimit", 0.0)) != float(timelimit):
            continue
        key = r["prob_hash"]
        grouped.setdefault(key, {})[r["arm"]] = r

    hashes = [h for h in sorted(grouped) if h in features]
    arms = sorted({arm for g in grouped.values() for arm in g})
    complete = [h for h in hashes if all(a in grouped[h] for a in arms)]
    if not complete:
        raise SystemExit("no complete feature/run rows found")

    frows = [features[h] for h in complete]
    names = _feature_names(frows)
    X = np.array([[float(row.get(k, 0.0)) for k in names] for row in frows], dtype=np.float64)
    Y = np.zeros((len(complete), len(arms)), dtype=np.float64)
    objectives = np.zeros_like(Y)
    probs = []
    for i, h in enumerate(complete):
        probs.append(frows[i].get("name") or h)
        for j, arm in enumerate(arms):
            obj = float(grouped[h][arm].get("objective", float("inf")))
            if not math.isfinite(obj) or obj <= 0:
                obj = 1e30
            objectives[i, j] = obj
            Y[i, j] = math.log(obj)
    return probs, complete, names, arms, X, Y, objectives


def _standardize(X, mean=None, scale=None):
    if mean is None:
        mean = X.mean(axis=0)
    if scale is None:
        scale = X.std(axis=0)
        scale[scale < 1e-12] = 1.0
    return (X - mean) / scale, mean, scale


def _fit_ridge(X, Y, alpha: float):
    Xs, mean, scale = _standardize(X)
    Xb = np.column_stack([np.ones(Xs.shape[0]), Xs])
    reg = np.eye(Xb.shape[1]) * float(alpha)
    reg[0, 0] = 0.0
    coef = np.linalg.solve(Xb.T @ Xb + reg, Xb.T @ Y)
    return {"mean": mean, "scale": scale, "coef": coef}


def _predict(model, X):
    Xs, _, _ = _standardize(X, model["mean"], model["scale"])
    Xb = np.column_stack([np.ones(Xs.shape[0]), Xs])
    return Xb @ model["coef"]


def _loo_eval(X, Y, objectives, arms, alpha):
    n = X.shape[0]
    pred_choice = []
    pred_obj = []
    for i in range(n):
        mask = np.ones(n, dtype=bool)
        mask[i] = False
        model = _fit_ridge(X[mask], Y[mask], alpha)
        pred = _predict(model, X[i:i + 1])[0]
        j = int(np.argmin(pred))
        pred_choice.append(j)
        pred_obj.append(float(objectives[i, j]))

    pred_obj = np.array(pred_obj, dtype=np.float64)
    vbs_obj = objectives.min(axis=1)
    sbs_idx = int(np.argmin(objectives.mean(axis=0)))
    sbs_obj = objectives[:, sbs_idx]
    label_idx = objectives.argmin(axis=1)
    acc = float(np.mean(np.array(pred_choice) == label_idx))
    return {
        "loo_accuracy": acc,
        "loo_mean_obj": float(pred_obj.mean()),
        "loo_sum_obj": float(pred_obj.sum()),
        "vbs_mean_obj": float(vbs_obj.mean()),
        "vbs_sum_obj": float(vbs_obj.sum()),
        "sbs_arm": arms[sbs_idx],
        "sbs_mean_obj": float(sbs_obj.mean()),
        "sbs_sum_obj": float(sbs_obj.sum()),
        "selector_vs_sbs_pct": float((pred_obj.sum() / sbs_obj.sum() - 1.0) * 100.0),
        "selector_vs_vbs_pct": float((pred_obj.sum() / vbs_obj.sum() - 1.0) * 100.0),
        "vbs_vs_sbs_pct": float((vbs_obj.sum() / sbs_obj.sum() - 1.0) * 100.0),
        "pred_counts": {arms[j]: int(sum(1 for x in pred_choice if x == j)) for j in range(len(arms))},
        "label_counts": {arms[j]: int(np.sum(label_idx == j)) for j in range(len(arms))},
    }


def _save_model(path: Path, feature_names, arms, model, metrics, args):
    payload = {
        "schema": SCHEMA,
        "data_schema": DATA_SCHEMA,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "arm_set": args.arm_set,
        "timelimit": float(args.timelimit),
        "alpha": float(args.alpha),
        "feature_names": feature_names,
        "arms": arms,
        "mean": model["mean"].tolist(),
        "scale": model["scale"].tolist(),
        "coef": model["coef"].tolist(),
        "metrics": metrics,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def _write_report(path: Path, probs, arms, objectives, metrics, model, X):
    pred = _predict(model, X)
    chosen = pred.argmin(axis=1)
    label = objectives.argmin(axis=1)
    lines = [
        "# ML selector report",
        "",
        f"- rows: {len(probs)}",
        f"- arms: {', '.join(arms)}",
        f"- SBS arm: {metrics['sbs_arm']}",
        f"- VBS vs SBS: {metrics['vbs_vs_sbs_pct']:.2f}%",
        f"- LOO selector vs SBS: {metrics['selector_vs_sbs_pct']:.2f}%",
        f"- LOO selector vs VBS: {metrics['selector_vs_vbs_pct']:.2f}%",
        f"- LOO exact best-arm accuracy: {metrics['loo_accuracy']:.3f}",
        "",
        "## Label Counts",
        "",
    ]
    for arm, count in metrics["label_counts"].items():
        lines.append(f"- {arm}: {count}")
    lines += ["", "## Training-Set Predictions", ""]
    for i, prob in enumerate(probs):
        cj = int(chosen[i])
        lj = int(label[i])
        lines.append(
            f"- {prob}: pred={arms[cj]} obj={objectives[i, cj]:.0f}; "
            f"best={arms[lj]} obj={objectives[i, lj]:.0f}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="reports/ml_dataset")
    ap.add_argument("--arm-set", default="extended")
    ap.add_argument("--timelimit", type=float, default=60.0)
    ap.add_argument("--alpha", type=float, default=10.0)
    ap.add_argument("--model-out", default="reports/ml_dataset/selector_model.json")
    ap.add_argument("--report-out", default="reports/ml_dataset/selector_report.md")
    args = ap.parse_args(argv)

    data_dir = Path(args.data_dir)
    probs, hashes, names, arms, X, Y, objectives = _build_matrix(
        data_dir / "features.jsonl",
        data_dir / "runs.jsonl",
        args.arm_set,
        args.timelimit,
    )
    metrics = _loo_eval(X, Y, objectives, arms, args.alpha)
    model = _fit_ridge(X, Y, args.alpha)
    _save_model(Path(args.model_out), names, arms, model, metrics, args)
    _write_report(Path(args.report_out), probs, arms, objectives, metrics, model, X)
    print(json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
