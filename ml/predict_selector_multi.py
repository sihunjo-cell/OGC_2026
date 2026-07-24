#!/usr/bin/env python
"""Print selector ranking for a model that may include budget features."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np

from .dataset import extract_features


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("problem_json")
    ap.add_argument("--model", default="reports/ml_dataset/selector_model_multi.json")
    ap.add_argument("--timelimit", type=float, default=None)
    ap.add_argument("--top-k", type=int, default=8)
    args = ap.parse_args(argv)

    with Path(args.problem_json).open("r", encoding="utf-8") as f:
        prob_info = json.load(f)
    with Path(args.model).open("r", encoding="utf-8") as f:
        model = json.load(f)

    tl = args.timelimit
    if tl is None:
        tl = model.get("default_timelimit", 60.0)
        if tl == "multi":
            tl = 60.0
    feats = extract_features(prob_info)
    feats["budget_s"] = float(tl)
    feats["log_budget_s"] = math.log(max(1.0, float(tl)))

    x = np.array([float(feats.get(k, 0.0)) for k in model["feature_names"]], dtype=np.float64)
    mean = np.array(model["mean"], dtype=np.float64)
    scale = np.array(model["scale"], dtype=np.float64)
    coef = np.array(model["coef"], dtype=np.float64)
    xb = np.concatenate([[1.0], (x - mean) / scale])
    pred = xb @ coef
    scores = sorted(zip(model["arms"], [math.exp(float(v)) for v in pred]), key=lambda p: p[1])
    for i, (arm, obj) in enumerate(scores[:args.top_k], start=1):
        print(f"{i}\t{arm}\t{obj:.6g}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
