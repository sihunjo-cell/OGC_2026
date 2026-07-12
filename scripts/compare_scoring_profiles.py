"""Compare Phase 2 scoring profiles on one instance."""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Phase2 import DEFAULT_SCORING_PROFILE_PORTFOLIO

DEFAULT_COMPARE_PROFILES = DEFAULT_SCORING_PROFILE_PORTFOLIO

# FIXME: default comparison timelimit should be tuned after a broader sweep.
DEFAULT_TIMELIMIT_S = 20.0

COLUMNS = (
    "profile",
    "objective",
    "Z1",
    "forced",
    "realize_ms",
    "iters",
    "iters_per_sec",
    "ms_per_realize",
    "candidate_evals",
    "forced_risk_evals",
    "avg_selected_forced_risk",
)


def _load_problem(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _format_value(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        if math.isinf(value):
            return "inf"
        return f"{value:.3f}"
    return str(value)


def _print_table(rows: list[dict]) -> None:
    widths = {col: len(col) for col in COLUMNS}
    formatted = []
    for row in rows:
        rec = {}
        for col in COLUMNS:
            text = _format_value(row.get(col))
            rec[col] = text
            widths[col] = max(widths[col], len(text))
        formatted.append(rec)

    header = "  ".join(col.ljust(widths[col]) for col in COLUMNS)
    sep = "  ".join("-" * widths[col] for col in COLUMNS)
    print(header)
    print(sep)
    for row in formatted:
        print("  ".join(row[col].ljust(widths[col]) for col in COLUMNS))


def _run_profile(prob_info: dict, pre, profile: str, timelimit_s: float) -> dict:
    from Outer.alns import alns
    from Outer.portfolio import default_portfolio

    cfg = deepcopy(default_portfolio()[0])
    cfg.phase2.scoring_profile = profile
    cfg.phase2.phase2_scoring_profiles = None
    cfg.phase2.phase2_variant_topks = ()
    cfg.phase2.resolve_scoring_profile()

    start = time.perf_counter()
    s_best, stats = alns(
        prob_info,
        pre,
        budget_s=None,
        cfg=cfg,
        deadline=start + timelimit_s,
        deadline_s=timelimit_s,
        t0=start,
    )
    metrics = dict(getattr(s_best, "phase2_info", {}).get("scoring_metrics", {}) or {})
    return {
        "profile": metrics.get("profile", profile),
        "objective": getattr(s_best, "objective", None),
        "Z1": getattr(s_best, "Z1", None),
        "forced": getattr(s_best, "forced", None),
        "realize_ms": metrics.get("realize_ms"),
        "iters": stats.get("iters", 0),
        "iters_per_sec": metrics.get("iters_per_sec"),
        "ms_per_realize": metrics.get("ms_per_realize"),
        "candidate_evals": metrics.get("candidate_evals"),
        "forced_risk_evals": metrics.get("forced_risk_evals"),
        "avg_selected_forced_risk": metrics.get("avg_selected_forced_risk"),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("problem", help="Problem JSON path, e.g. train/prob_1.json")
    ap.add_argument("--profiles", nargs="+", default=list(DEFAULT_COMPARE_PROFILES))
    ap.add_argument("--timelimit", type=float, default=DEFAULT_TIMELIMIT_S)
    args = ap.parse_args()

    from Phase0 import preprocess

    prob_path = Path(args.problem)
    prob_info = _load_problem(str(prob_path))
    pre = preprocess(prob_info)

    rows = [_run_profile(prob_info, pre, profile, args.timelimit) for profile in args.profiles]
    _print_table(rows)


if __name__ == "__main__":
    main()
