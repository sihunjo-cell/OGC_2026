#!/usr/bin/env python
"""Build ML training data for per-instance solver configuration selection.

The generated dataset is deliberately simple:

* features.jsonl: cheap instance features computed from prob_info only.
* runs.jsonl: objective observed for each solver arm.
* labels.jsonl: best arm for each instance and timelimit.

Default command:

    python -m ml.dataset --prob-dir train --timelimit 60

The script is append-only and resume-safe. Re-running the same command skips
completed (problem, arm, timelimit, arm-set) records unless --force is passed.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import math
import os
import sys
import time
from copy import deepcopy
from pathlib import Path
from random import Random
from statistics import mean, median

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Phase0 import preprocess
from Phase2 import Phase2Config
from Outer.alns import alns
from Outer.config import OuterConfig
from Outer.portfolio import default_portfolio

SCHEMA = "ogc-ml-config-v1"


def _shoelace(ring) -> float:
    if len(ring) < 3:
        return 0.0
    s = 0.0
    for i, (x0, y0) in enumerate(ring):
        x1, y1 = ring[(i + 1) % len(ring)]
        s += x0 * y1 - x1 * y0
    return abs(s) * 0.5


def _bbox(ring):
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    return min(xs), min(ys), max(xs), max(ys)


def _stats(xs):
    xs = [float(x) for x in xs]
    if not xs:
        return {
            "min": 0.0,
            "max": 0.0,
            "mean": 0.0,
            "median": 0.0,
            "std": 0.0,
            "sum": 0.0,
        }
    mu = mean(xs)
    var = mean([(x - mu) * (x - mu) for x in xs])
    return {
        "min": min(xs),
        "max": max(xs),
        "mean": mu,
        "median": median(xs),
        "std": math.sqrt(var),
        "sum": sum(xs),
    }


def _add_stats(out, prefix, xs):
    for k, v in _stats(xs).items():
        out[f"{prefix}_{k}"] = v


def _prob_hash(prob_info: dict) -> str:
    payload = json.dumps(prob_info, sort_keys=True, separators=(",", ":"))
    return hashlib.blake2b(payload.encode("utf-8"), digest_size=16).hexdigest()


def extract_features(prob_info: dict) -> dict:
    """Cheap fixed-width features for algorithm/config selection."""
    blocks = prob_info["blocks"]
    bays = prob_info["bays"]
    weights = prob_info.get("weights", {})
    n = len(blocks)
    m = len(bays)

    out = {
        "schema": SCHEMA,
        "name": prob_info.get("name", ""),
        "prob_hash": _prob_hash(prob_info),
        "n_blocks": n,
        "n_bays": m,
        "w1": float(weights.get("w1", 1.0)),
        "w2": float(weights.get("w2", 1.0)),
        "w3": float(weights.get("w3", 1.0)),
    }

    bay_area = [b["width"] * b["height"] for b in bays]
    _add_stats(out, "bay_area", bay_area)
    _add_stats(out, "bay_width", [b["width"] for b in bays])
    _add_stats(out, "bay_height", [b["height"] for b in bays])

    release = [b["release_time"] for b in blocks]
    due = [b["due_date"] for b in blocks]
    proc = [b["processing_time"] for b in blocks]
    workload = [b["workload"] for b in blocks]
    slack = [d - r - p for r, d, p in zip(release, due, proc)]
    _add_stats(out, "release", release)
    _add_stats(out, "due", due)
    _add_stats(out, "proc", proc)
    _add_stats(out, "workload", workload)
    _add_stats(out, "slack", slack)
    out["horizon"] = (max(due) - min(release)) if blocks else 0.0
    out["load_density"] = (
        sum(workload) / max(1.0, sum(bay_area) * max(1.0, out["horizon"]))
    )

    best_pref = []
    pref_gap = []
    pref_std = []
    orient_count = []
    layer_count = []
    footprint_area = []
    bbox_area = []
    aspect = []
    eligible_raw = []

    for b in blocks:
        prefs = [float(x) for x in b.get("bay_preferences", [])]
        if prefs:
            sp = sorted(prefs, reverse=True)
            best_pref.append(sp[0])
            pref_gap.append(sp[0] - (sp[1] if len(sp) > 1 else 0.0))
            pref_std.append(_stats(prefs)["std"])
        shapes = b.get("shape", [])
        orient_count.append(len(shapes))
        best_bbox_area = None
        best_fp_area = None
        best_aspect = 0.0
        max_layers = 0
        for s in shapes:
            layers = s.get("layers", [])
            max_layers = max(max_layers, len(layers))
            if not layers:
                continue
            # Use the largest layer as floor proxy, matching Phase1.common.
            layer_areas = [_shoelace(layer) for layer in layers]
            fp = max(layer_areas) if layer_areas else 0.0
            x0, y0, x1, y1 = _bbox(layers[0])
            bw = max(0.0, x1 - x0)
            bh = max(0.0, y1 - y0)
            ba = bw * bh
            if best_bbox_area is None or ba < best_bbox_area:
                best_bbox_area = ba
                best_fp_area = fp
                best_aspect = max(bw, bh) / max(1e-9, min(bw, bh))
        layer_count.append(max_layers)
        footprint_area.append(best_fp_area or 0.0)
        bbox_area.append(best_bbox_area or 0.0)
        aspect.append(best_aspect)
        eligible_raw.append(sum(1 for a in bay_area if (best_bbox_area or 0.0) <= a))

    _add_stats(out, "best_pref", best_pref)
    _add_stats(out, "pref_gap", pref_gap)
    _add_stats(out, "pref_std", pref_std)
    _add_stats(out, "orient_count", orient_count)
    _add_stats(out, "layer_count", layer_count)
    _add_stats(out, "footprint_area", footprint_area)
    _add_stats(out, "bbox_area", bbox_area)
    _add_stats(out, "aspect", aspect)
    _add_stats(out, "eligible_raw", eligible_raw)

    total_fp_time = sum(a * p for a, p in zip(footprint_area, proc))
    out["area_time_density"] = total_fp_time / max(1.0, sum(bay_area) * max(1.0, out["horizon"]))
    out["bbox_fill_mean"] = mean(
        [a / max(1.0, max(bay_area)) for a in bbox_area]
    ) if bbox_area else 0.0
    return out


def _cfg_to_record(cfg: OuterConfig) -> dict:
    rec = dataclasses.asdict(cfg)
    p2 = rec.get("phase2")
    if dataclasses.is_dataclass(cfg.phase2):
        p2 = dataclasses.asdict(cfg.phase2)
    rec["phase2"] = p2
    return rec


def build_arms(prob_info: dict, arm_set: str):
    arms = [(f"pf{i}", deepcopy(c)) for i, c in enumerate(default_portfolio(prob_info))]
    if arm_set == "default":
        return arms
    if arm_set != "extended":
        raise ValueError(f"unknown arm set: {arm_set}")

    p0 = deepcopy(arms[0][1].phase2)
    p1 = deepcopy(arms[2][1].phase2)
    arms.extend([
        (
            "inbay_light_k3",
            OuterConfig(
                xi=0.3,
                seed=1,
                restart_stall=8,
                inbay_stall=8,
                inbay_realize_k=2,
                inbay_bays=1,
                inbay_top=8,
                phase2=p0,
            ),
        ),
        (
            "inbay_light_k1",
            OuterConfig(
                xi=0.5,
                seed=5,
                restart_stall=8,
                restart_flip=1,
                inbay_stall=6,
                inbay_realize_k=2,
                inbay_bays=1,
                inbay_top=8,
                phase2=p1,
            ),
        ),
        (
            "dyn_off_k3",
            OuterConfig(
                xi=0.3,
                seed=1,
                restart_stall=8,
                phase2=Phase2Config(
                    atc_kappa=3.0,
                    dispatch_admit_fail_stop=8,
                    dispatch_dynamic_bay=False,
                ),
            ),
        ),
        (
            "dyn_off_k1",
            OuterConfig(
                xi=0.5,
                seed=5,
                restart_stall=16,
                phase2=Phase2Config(
                    atc_kappa=1.0,
                    dispatch_admit_fail_stop=24,
                    dispatch_dynamic_bay=False,
                ),
            ),
        ),
    ])
    return arms


def _append_jsonl(path: Path, rec: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())


def _load_done(path: Path) -> set[tuple]:
    done = set()
    if not path.exists():
        return done
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("schema") != SCHEMA:
                continue
            key = (r.get("prob"), r.get("arm_set"), r.get("arm"), float(r.get("timelimit", 0.0)))
            done.add(key)
    return done


def _natural_key(path: Path):
    digits = "".join(ch if ch.isdigit() else " " for ch in path.stem).split()
    return (int(digits[-1]) if digits else 0, path.name)


def run_arm(prob_info: dict, cfg: OuterConfig, timelimit: float):
    start = time.perf_counter()
    pre = preprocess(prob_info)
    deadline = start + max(1.0, timelimit)
    stats = {}
    try:
        s, stats = alns(
            prob_info,
            pre,
            budget_s=None,
            cfg=cfg,
            deadline=deadline,
            deadline_s=timelimit,
        )
        obj = float(s.objective) if s.feasible else float("inf")
        sol = s.solution if s.feasible else None
        feasible = bool(s.feasible)
        z1, z2, z3 = s.Z1, s.Z2, s.Z3
        err = None
    except Exception as exc:
        obj = float("inf")
        sol = None
        feasible = False
        z1 = z2 = z3 = None
        err = repr(exc)
    return {
        "objective": obj,
        "feasible": feasible,
        "Z1": z1,
        "Z2": z2,
        "Z3": z3,
        "wall_s": round(time.perf_counter() - start, 3),
        "stats": stats,
        "solution_present": sol is not None,
        "error": err,
    }


def write_label(labels_path: Path, prob_name: str, prob_hash: str, arm_set: str,
                timelimit: float, run_records: list[dict]) -> None:
    valid = [r for r in run_records if math.isfinite(float(r["objective"]))]
    best = min(valid, key=lambda r: r["objective"]) if valid else None
    rec = {
        "schema": SCHEMA,
        "prob": prob_name,
        "prob_hash": prob_hash,
        "arm_set": arm_set,
        "timelimit": float(timelimit),
        "best_arm": None if best is None else best["arm"],
        "best_objective": None if best is None else best["objective"],
        "arms": [
            {
                "arm": r["arm"],
                "objective": r["objective"],
                "feasible": r["feasible"],
                "wall_s": r["wall_s"],
            }
            for r in run_records
        ],
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    _append_jsonl(labels_path, rec)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prob-dir", default="train")
    ap.add_argument("--out-dir", default="reports/ml_dataset")
    ap.add_argument("--timelimit", type=float, default=60.0)
    ap.add_argument("--arm-set", choices=("default", "extended"), default="default")
    ap.add_argument("--limit", type=int, default=0, help="only first N instances")
    ap.add_argument("--shuffle", action="store_true")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)

    prob_dir = Path(args.prob_dir)
    out_dir = Path(args.out_dir)
    runs_path = out_dir / "runs.jsonl"
    features_path = out_dir / "features.jsonl"
    labels_path = out_dir / "labels.jsonl"
    manifest_path = out_dir / "manifest.json"

    paths = sorted(prob_dir.glob("*.json"), key=_natural_key)
    if args.shuffle:
        rng = Random(args.seed)
        rng.shuffle(paths)
    if args.limit > 0:
        paths = paths[:args.limit]
    if not paths:
        raise SystemExit(f"no problem json files found in {prob_dir}")

    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema": SCHEMA,
        "prob_dir": str(prob_dir),
        "out_dir": str(out_dir),
        "timelimit": args.timelimit,
        "arm_set": args.arm_set,
        "n_instances": len(paths),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "command": " ".join(sys.argv),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    done = set() if args.force else _load_done(runs_path)
    for pi, path in enumerate(paths, start=1):
        with path.open("r", encoding="utf-8") as f:
            prob_info = json.load(f)
        prob_name = prob_info.get("name") or path.stem
        features = extract_features(prob_info)
        features["source_path"] = str(path)
        features["created_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        _append_jsonl(features_path, features)

        arms = build_arms(prob_info, args.arm_set)
        prob_records = []
        print(f"[{pi}/{len(paths)}] {prob_name}: {len(arms)} arms, T={args.timelimit:g}s", flush=True)
        for arm_name, cfg in arms:
            key = (prob_name, args.arm_set, arm_name, float(args.timelimit))
            if key in done:
                print(f"  skip {arm_name}", flush=True)
                continue
            print(f"  run  {arm_name}", flush=True)
            res = run_arm(prob_info, cfg, args.timelimit)
            rec = {
                "schema": SCHEMA,
                "prob": prob_name,
                "prob_hash": features["prob_hash"],
                "source_path": str(path),
                "arm_set": args.arm_set,
                "arm": arm_name,
                "timelimit": float(args.timelimit),
                "config": _cfg_to_record(cfg),
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                **res,
            }
            _append_jsonl(runs_path, rec)
            prob_records.append(rec)
        # Build label from records generated in this process. A full label is
        # emitted only when all arms for this problem were run now.
        if len(prob_records) == len(arms):
            write_label(
                labels_path,
                prob_name,
                features["prob_hash"],
                args.arm_set,
                args.timelimit,
                prob_records,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
