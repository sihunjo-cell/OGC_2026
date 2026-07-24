#!/usr/bin/env python
"""Print learned selector arm ranking for one problem JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .selector import load_model, predict_arm_scores


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("problem_json")
    ap.add_argument("--model", default="reports/ml_dataset/selector_model.json")
    ap.add_argument("--top-k", type=int, default=8)
    args = ap.parse_args(argv)

    with Path(args.problem_json).open("r", encoding="utf-8") as f:
        prob_info = json.load(f)
    model = load_model(args.model)
    scores = predict_arm_scores(prob_info, model)
    for i, (arm, pred_obj) in enumerate(scores[:args.top_k], start=1):
        print(f"{i}\t{arm}\t{pred_obj:.6g}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
