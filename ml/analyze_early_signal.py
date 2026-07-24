#!/usr/bin/env python
"""Measure whether early/initial performance predicts long-budget performance."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np

DATA_SCHEMA = "ogc-ml-config-v1"


def _labels(path: Path, arm_set: str, timelimit: float):
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
            out[rec["prob"]] = {
                a["arm"]: float(a["objective"])
                for a in rec.get("arms", [])
                if math.isfinite(float(a["objective"]))
            }
    return out


def _rank(vals: dict[str, float], arms: list[str]):
    return sorted(arms, key=lambda a: vals[a])


def _spearman(x, y):
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    if x.size < 2:
        return 0.0
    rx = np.argsort(np.argsort(x)).astype(np.float64)
    ry = np.argsort(np.argsort(y)).astype(np.float64)
    if rx.std() < 1e-12 or ry.std() < 1e-12:
        return 0.0
    return float(np.corrcoef(rx, ry)[0, 1])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--early-dir", default="reports/ml_dataset")
    ap.add_argument("--early-timelimit", type=float, default=60.0)
    ap.add_argument("--final-dir", default="reports/ml_dataset_focus")
    ap.add_argument("--final-timelimit", type=float, default=600.0)
    ap.add_argument("--arm-set", default="extended")
    ap.add_argument("--arms", default="pf0,pf1,pf2,pf3")
    args = ap.parse_args(argv)

    early = _labels(Path(args.early_dir) / "labels.jsonl", args.arm_set, args.early_timelimit)
    final = _labels(Path(args.final_dir) / "labels.jsonl", args.arm_set, args.final_timelimit)
    probs = sorted(set(early) & set(final))
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    probs = [p for p in probs if all(a in early[p] and a in final[p] for a in arms)]
    if not probs:
        raise SystemExit("no overlapping complete rows")

    top1 = top2 = 0
    spears = []
    final_best_after_early = 0.0
    final_best_oracle = 0.0
    final_pf3 = 0.0

    print("prob,early_order,final_order,early_best,final_best,early_best_final_regret_pct,spearman")
    for p in probs:
        er = _rank(early[p], arms)
        fr = _rank(final[p], arms)
        eb, fb = er[0], fr[0]
        if eb == fb:
            top1 += 1
        if fb in er[:2]:
            top2 += 1
        sx = [early[p][a] for a in arms]
        sy = [final[p][a] for a in arms]
        sp = _spearman(sx, sy)
        spears.append(sp)
        final_best_after_early += final[p][eb]
        final_best_oracle += final[p][fb]
        final_pf3 += final[p].get("pf3", final[p][fb])
        regret = (final[p][eb] / final[p][fb] - 1.0) * 100.0
        print(f"{p},{'|'.join(er)},{'|'.join(fr)},{eb},{fb},{regret:.2f},{sp:.3f}")

    print()
    print(f"rows={len(probs)}")
    print(f"early top1 matches final top1: {top1}/{len(probs)}")
    print(f"final top1 is within early top2: {top2}/{len(probs)}")
    print(f"mean spearman={sum(spears) / len(spears):.3f}")
    print(f"early-winner final sum={final_best_after_early:.0f}")
    print(f"oracle final sum={final_best_oracle:.0f}")
    print(f"pf3 final sum={final_pf3:.0f}")
    print(f"early-winner vs pf3={(final_best_after_early / final_pf3 - 1.0) * 100.0:.2f}%")
    print(f"oracle vs pf3={(final_best_oracle / final_pf3 - 1.0) * 100.0:.2f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
