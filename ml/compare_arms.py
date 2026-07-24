#!/usr/bin/env python
"""Detailed arm comparison from labels.jsonl."""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path

DATA_SCHEMA = "ogc-ml-config-v1"


def _rows(path: Path, arm_set: str, timelimit: float):
    out = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("schema") != DATA_SCHEMA:
                continue
            if r.get("arm_set") != arm_set:
                continue
            if float(r.get("timelimit", 0.0)) != float(timelimit):
                continue
            out.append(r)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="reports/ml_dataset_focus")
    ap.add_argument("--arm-set", default="extended")
    ap.add_argument("--timelimit", type=float, default=600.0)
    ap.add_argument("--baseline-arm", default="pf3")
    args = ap.parse_args(argv)

    rows = _rows(Path(args.data_dir) / "labels.jsonl", args.arm_set, args.timelimit)
    if not rows:
        raise SystemExit("no matching labels found")
    arms = [a["arm"] for a in rows[0]["arms"]]
    sums = {a: 0.0 for a in arms}
    wins = Counter()
    print("prob,best,best_obj,baseline_obj,baseline_regret_pct")
    for r in rows:
        vals = {a["arm"]: float(a["objective"]) for a in r["arms"]}
        for arm, obj in vals.items():
            if math.isfinite(obj):
                sums[arm] += obj
        best = min(vals, key=vals.get)
        wins[best] += 1
        base = vals[args.baseline_arm]
        best_obj = vals[best]
        regret = (base / best_obj - 1.0) * 100.0 if best_obj > 0 else 0.0
        print(f"{r['prob']},{best},{best_obj:.0f},{base:.0f},{regret:.2f}")

    print("\narm,total_obj,vs_best_arm_pct,wins")
    best_sum = min(sums.values())
    for arm, total in sorted(sums.items(), key=lambda kv: kv[1]):
        print(f"{arm},{total:.0f},{(total / best_sum - 1.0) * 100.0:.2f},{wins[arm]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
