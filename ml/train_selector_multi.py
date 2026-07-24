#!/usr/bin/env python
"""Train the solver-arm selector on multiple timelimits at once.

Use this after collecting additional labels, for example:

    python -m ml.make_dataset --prob-dir train --timelimit 120 --arm-set extended
    python -m ml.make_dataset --prob-dir train --timelimit 180 --arm-set extended
    python -m ml.train_selector_multi --timelimits 60,120,180

The timelimit is appended as two extra features (`budget_s`,
`log_budget_s`), so one model can learn that a configuration may be good at
short budgets and bad at long budgets.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

import numpy as np

from . import train_selector as ts


def _parse_timelimits(text: str) -> list[float]:
    vals = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        vals.append(float(part))
    if not vals:
        raise ValueError("empty --timelimits")
    return vals


def _load_one(data_dir: Path, arm_set: str, timelimit: float):
    probs, hashes, names, arms, X, Y, objectives = ts._build_matrix(
        data_dir / "features.jsonl",
        data_dir / "runs.jsonl",
        arm_set,
        timelimit,
    )
    budget = np.full((X.shape[0], 1), float(timelimit), dtype=np.float64)
    log_budget = np.full((X.shape[0], 1), math.log(max(1.0, float(timelimit))), dtype=np.float64)
    X2 = np.column_stack([X, budget, log_budget])
    return probs, hashes, names + ["budget_s", "log_budget_s"], arms, X2, Y, objectives


def _save_model(path: Path, feature_names, arms, model, metrics, args, timelimits):
    payload = {
        "schema": ts.SCHEMA,
        "data_schema": ts.DATA_SCHEMA,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "arm_set": args.arm_set,
        "timelimit": "multi",
        "timelimits": timelimits,
        "default_timelimit": max(timelimits),
        "alpha": float(args.alpha),
        "feature_names": feature_names,
        "arms": arms,
        "mean": model["mean"].tolist(),
        "scale": model["scale"].tolist(),
        "coef": model["coef"].tolist(),
        "metrics": metrics,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="reports/ml_dataset")
    ap.add_argument("--arm-set", default="extended")
    ap.add_argument("--timelimits", default="60")
    ap.add_argument("--alpha", type=float, default=1000.0)
    ap.add_argument("--model-out", default="reports/ml_dataset/selector_model_multi.json")
    ap.add_argument("--report-out", default="reports/ml_dataset/selector_report_multi.md")
    args = ap.parse_args(argv)

    data_dir = Path(args.data_dir)
    tls = _parse_timelimits(args.timelimits)
    chunks = []
    for tl in tls:
        try:
            chunks.append(_load_one(data_dir, args.arm_set, tl))
        except SystemExit:
            print(f"skip missing/incomplete timelimit={tl:g}")
    if not chunks:
        raise SystemExit("no complete timelimit chunks found")

    arms = chunks[0][3]
    names = chunks[0][2]
    for ch in chunks:
        if ch[2] != names or ch[3] != arms:
            raise SystemExit("feature or arm mismatch across timelimits")

    probs = []
    Xs = []
    Ys = []
    Os = []
    for tl, ch in zip(tls, chunks):
        p, _h, _n, _a, X, Y, O = ch
        probs.extend([f"{name}@{tl:g}s" for name in p])
        Xs.append(X)
        Ys.append(Y)
        Os.append(O)
    X = np.vstack(Xs)
    Y = np.vstack(Ys)
    objectives = np.vstack(Os)

    metrics = ts._loo_eval(X, Y, objectives, arms, args.alpha)
    model = ts._fit_ridge(X, Y, args.alpha)
    _save_model(Path(args.model_out), names, arms, model, metrics, args, tls)
    ts._write_report(Path(args.report_out), probs, arms, objectives, metrics, model, X)
    print(json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
