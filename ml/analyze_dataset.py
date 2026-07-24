#!/usr/bin/env python
"""Summarize ML selector labels and oracle gaps."""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from pathlib import Path


DATA_SCHEMA = "ogc-ml-config-v1"


def _read_labels(path: Path):
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


def _summarize(rows):
    groups = defaultdict(list)
    for r in rows:
        groups[(r.get("arm_set"), float(r.get("timelimit", 0.0)))].append(r)
    for (arm_set, tl), rs in sorted(groups.items(), key=lambda x: (x[0][0], x[0][1])):
        arms = sorted({a["arm"] for r in rs for a in r.get("arms", [])})
        by_arm = {a: [] for a in arms}
        vbs = []
        label_counts = Counter()
        for r in rs:
            vals = {a["arm"]: float(a["objective"]) for a in r.get("arms", [])}
            valid = {k: v for k, v in vals.items() if math.isfinite(v)}
            if not valid:
                continue
            best_arm = min(valid, key=valid.get)
            label_counts[best_arm] += 1
            vbs.append(valid[best_arm])
            for arm in arms:
                by_arm[arm].append(valid.get(arm, float("inf")))
        sbs_arm = min(arms, key=lambda a: sum(by_arm[a]) / max(1, len(by_arm[a])))
        sbs = by_arm[sbs_arm]
        vbs_sum = sum(vbs)
        sbs_sum = sum(sbs)
        print(f"\n[{arm_set} T={tl:g}s] rows={len(rs)}")
        print(f"  SBS={sbs_arm} sum={sbs_sum:.0f}")
        print(f"  VBS sum={vbs_sum:.0f} gap={(vbs_sum / sbs_sum - 1.0) * 100.0:.2f}%")
        print("  labels:")
        for arm, count in label_counts.most_common():
            print(f"    {arm}: {count}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="reports/ml_dataset")
    args = ap.parse_args(argv)
    rows = _read_labels(Path(args.data_dir) / "labels.jsonl")
    if not rows:
        raise SystemExit("no labels found")
    _summarize(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
