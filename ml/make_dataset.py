#!/usr/bin/env python
"""Resume-safe command wrapper for ML config-selection data generation."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

from . import dataset as ds


def _run_key(rec):
    return (
        rec.get("prob"),
        rec.get("arm_set"),
        rec.get("arm"),
        float(rec.get("timelimit", 0.0)),
    )


def _label_key(rec):
    return (rec.get("prob"), rec.get("arm_set"), float(rec.get("timelimit", 0.0)))


def _load_jsonl(path: Path):
    if not path.exists():
        return []
    out = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("schema") == ds.SCHEMA:
                out.append(rec)
    return out


def _latest_runs(path: Path):
    out = {}
    for rec in _load_jsonl(path):
        out[_run_key(rec)] = rec
    return out


def _done_labels(path: Path):
    return {_label_key(rec) for rec in _load_jsonl(path)}


def _append(path: Path, rec: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())


def _paths(prob_dir: Path, limit: int):
    paths = sorted(prob_dir.glob("*.json"), key=ds._natural_key)
    return paths[:limit] if limit > 0 else paths


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prob-dir", default="train")
    ap.add_argument("--out-dir", default="reports/ml_dataset")
    ap.add_argument("--timelimit", type=float, default=60.0)
    ap.add_argument("--arm-set", choices=("default", "extended"), default="default")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)

    prob_dir = Path(args.prob_dir)
    out_dir = Path(args.out_dir)
    runs_path = out_dir / "runs.jsonl"
    features_path = out_dir / "features.jsonl"
    labels_path = out_dir / "labels.jsonl"
    manifest_path = out_dir / "manifest.json"
    out_dir.mkdir(parents=True, exist_ok=True)

    paths = _paths(prob_dir, args.limit)
    if not paths:
        raise SystemExit(f"no problem json files found in {prob_dir}")

    manifest = {
        "schema": ds.SCHEMA,
        "prob_dir": str(prob_dir),
        "out_dir": str(out_dir),
        "timelimit": args.timelimit,
        "arm_set": args.arm_set,
        "n_instances": len(paths),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "command": " ".join(sys.argv),
        "entrypoint": "ml.make_dataset",
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    existing = {} if args.force else _latest_runs(runs_path)
    labels_done = set() if args.force else _done_labels(labels_path)

    for idx, path in enumerate(paths, start=1):
        with path.open("r", encoding="utf-8") as f:
            prob_info = json.load(f)
        prob = prob_info.get("name") or path.stem
        features = ds.extract_features(prob_info)
        features["source_path"] = str(path)
        features["created_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        _append(features_path, features)

        arms = ds.build_arms(prob_info, args.arm_set)
        print(f"[{idx}/{len(paths)}] {prob}: {len(arms)} arms, T={args.timelimit:g}s", flush=True)
        for arm_name, cfg in arms:
            key = (prob, args.arm_set, arm_name, float(args.timelimit))
            if key in existing:
                print(f"  skip {arm_name}", flush=True)
                continue
            print(f"  run  {arm_name}", flush=True)
            res = ds.run_arm(prob_info, cfg, args.timelimit)
            rec = {
                "schema": ds.SCHEMA,
                "prob": prob,
                "prob_hash": features["prob_hash"],
                "source_path": str(path),
                "arm_set": args.arm_set,
                "arm": arm_name,
                "timelimit": float(args.timelimit),
                "config": ds._cfg_to_record(cfg),
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                **res,
            }
            _append(runs_path, rec)
            existing[key] = rec

        records = [
            existing.get((prob, args.arm_set, arm_name, float(args.timelimit)))
            for arm_name, _cfg in arms
        ]
        if all(records):
            lkey = (prob, args.arm_set, float(args.timelimit))
            if args.force or lkey not in labels_done:
                valid = [r for r in records if math.isfinite(float(r["objective"]))]
                best = min(valid, key=lambda r: r["objective"]) if valid else None
                label = {
                    "schema": ds.SCHEMA,
                    "prob": prob,
                    "prob_hash": features["prob_hash"],
                    "arm_set": args.arm_set,
                    "timelimit": float(args.timelimit),
                    "best_arm": None if best is None else best["arm"],
                    "best_objective": None if best is None else best["objective"],
                    "arms": [
                        {
                            "arm": r["arm"],
                            "objective": r["objective"],
                            "feasible": r["feasible"],
                            "wall_s": r["wall_s"],
                        }
                        for r in records
                    ],
                    "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                }
                _append(labels_path, label)
                labels_done.add(lkey)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
