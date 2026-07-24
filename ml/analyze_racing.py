#!/usr/bin/env python
"""Compare early-runtime arm racing against final long-budget results.

This answers a practical question:

    If we run each arm briefly, can early telemetry pick the best 600s arm?

Inputs are two existing datasets:

* early data, e.g. reports/ml_dataset at T=60
* final data, e.g. reports/ml_dataset_focus at T=600

The script evaluates simple racing policies before building a learned model:

* early_best: choose the arm with the best early objective.
* early_best_core: same, restricted to pf0..pf3 by default.
* sbs_final: single best final arm over the final dataset.
* vbs_final: oracle per-instance final best.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

DATA_SCHEMA = "ogc-ml-config-v1"


def _load_labels(path: Path, arm_set: str, timelimit: float):
    out = {}
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("schema") != DATA_SCHEMA:
                continue
            if rec.get("arm_set") != arm_set:
                continue
            if float(rec.get("timelimit", 0.0)) != float(timelimit):
                continue
            vals = {
                a["arm"]: float(a["objective"])
                for a in rec.get("arms", [])
                if math.isfinite(float(a["objective"]))
            }
            out[rec["prob"]] = vals
    return out


def _best_arm(vals: dict[str, float], allowed=None):
    if allowed is not None:
        vals = {k: v for k, v in vals.items() if k in allowed}
    return min(vals, key=vals.get)


def _sum_for_choice(final, choices):
    total = 0.0
    rows = []
    for prob, arm in choices.items():
        obj = final[prob][arm]
        total += obj
        rows.append((prob, arm, obj))
    return total, rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--early-dir", default="reports/ml_dataset")
    ap.add_argument("--early-timelimit", type=float, default=60.0)
    ap.add_argument("--final-dir", default="reports/ml_dataset_focus")
    ap.add_argument("--final-timelimit", type=float, default=600.0)
    ap.add_argument("--arm-set", default="extended")
    ap.add_argument("--core-arms", default="pf0,pf1,pf2,pf3")
    args = ap.parse_args(argv)

    early = _load_labels(Path(args.early_dir) / "labels.jsonl", args.arm_set, args.early_timelimit)
    final = _load_labels(Path(args.final_dir) / "labels.jsonl", args.arm_set, args.final_timelimit)
    probs = sorted(set(early) & set(final))
    if not probs:
        raise SystemExit("no overlapping problems found")

    arms = sorted({a for p in probs for a in final[p]})
    core = {x.strip() for x in args.core_arms.split(",") if x.strip()}

    final_sums = {arm: sum(final[p][arm] for p in probs) for arm in arms}
    sbs_arm = min(final_sums, key=final_sums.get)
    sbs_sum = final_sums[sbs_arm]
    vbs_choices = {p: _best_arm(final[p]) for p in probs}
    vbs_sum, _ = _sum_for_choice(final, vbs_choices)

    early_choices = {p: _best_arm(early[p]) for p in probs}
    early_sum, early_rows = _sum_for_choice(final, early_choices)

    core_choices = {p: _best_arm(early[p], core) for p in probs}
    core_sum, core_rows = _sum_for_choice(final, core_choices)

    print(f"rows={len(probs)}")
    print(f"final_sbs={sbs_arm} sum={sbs_sum:.0f}")
    print(f"final_vbs sum={vbs_sum:.0f} vs_sbs={(vbs_sum / sbs_sum - 1.0) * 100.0:.2f}%")
    print(f"early_best sum={early_sum:.0f} vs_sbs={(early_sum / sbs_sum - 1.0) * 100.0:.2f}%")
    print(f"early_best_core sum={core_sum:.0f} vs_sbs={(core_sum / sbs_sum - 1.0) * 100.0:.2f}%")
    print()
    print("prob,early_best,early_best_final_obj,early_core,early_core_final_obj,final_best,final_best_obj")
    for p in probs:
        fb = vbs_choices[p]
        eb = early_choices[p]
        cb = core_choices[p]
        print(
            f"{p},{eb},{final[p][eb]:.0f},"
            f"{cb},{final[p][cb]:.0f},"
            f"{fb},{final[p][fb]:.0f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
