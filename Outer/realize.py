"""
Outer.realize -- evaluate a bay assignment as a complete Solution.

The bay assignment is fixed first, then Phase 2 is run to produce a full layout.
When enabled, multiple complete Phase 2 variants are decoded and the final choice
is made by the actual solution objective, not by local placement heuristics.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from copy import deepcopy

from Phase1.timing import init_timing
from Phase2 import PlaceAndCrane, Phase2Config
from Phase2.crane import _load_utils
from .state import Solution

_BIG = 1e18


def _objective_from_parts(prob_info: dict, z1: float, z2: float, z3: float) -> float:
    w = prob_info.get("weights", {})
    w1 = w.get("w1", 1.0)
    w2 = w.get("w2", 1.0)
    w3 = w.get("w3", 1.0)
    return w1 * z1 + w2 * z2 + w3 * z3


def _phase2_variant_topks(cfg) -> tuple:
    topks = getattr(cfg, "phase2_variant_topks", (4, 8, 16))  # FIXME
    out = []
    for value in topks or ():
        try:
            out.append(max(1, int(value)))
        except Exception:
            continue
    return tuple(out)


def _debug_phase2_variants(cfg) -> bool:
    flag = getattr(cfg, "debug_phase2_variants", None)
    if flag is not None:
        return bool(flag)
    env = os.environ.get("OGC_DEBUG_PHASE2_VARIANTS", "")
    return env.lower() in {"1", "true", "yes", "on"}


def _solution_ops_fingerprint(solution: dict) -> tuple[str, int]:
    if not solution or "operations" not in solution:
        return "none", 0
    rows = []
    for day, ops in sorted(solution["operations"].items(), key=lambda item: int(item[0])):
        day_i = int(day)
        for op in ops:
            rows.append((
                day_i,
                op.get("type"),
                op.get("block_id"),
                op.get("bay_id"),
                op.get("x"),
                op.get("y"),
                op.get("orient_idx"),
            ))
    payload = json.dumps(rows, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha1(payload.encode("ascii")).hexdigest()[:16], len(rows)


def _emit_variant_debug(event: str, record: dict) -> None:
    sys.stderr.write(f"[phase2-variant] {event} {json.dumps(record, sort_keys=True)}\n")
    sys.stderr.flush()


def _build_variant_cfgs(base_cfg, debug_enabled: bool) -> list:
    cfgs = []
    base = deepcopy(base_cfg)
    base_top_k = max(1, int(getattr(base, "contact_exact_top_k", 8)))
    base._variant_id = "baseline"
    base._variant_kind = "baseline"
    base._debug_variant_stats = {}
    base._debug_phase2_variants = debug_enabled
    cfgs.append(base)

    seen = {base_top_k}
    for top_k in _phase2_variant_topks(base_cfg):
        if top_k in seen:
            continue
        seen.add(top_k)
        cfg = deepcopy(base_cfg)
        cfg.contact_exact_top_k = top_k
        cfg._variant_id = f"topk_{top_k}"
        cfg._variant_kind = "extra"
        cfg._debug_variant_stats = {}
        cfg._debug_phase2_variants = debug_enabled
        cfgs.append(cfg)
    return cfgs


def _solution_from_result(bay: list, p1, res, prob_info: dict, utils_mod=None) -> tuple[Solution, dict]:
    z2 = p1.Z2 if p1.Z2 is not None else 0.0
    z3 = p1.Z3 if p1.Z3 is not None else 0.0
    feasible = bool(res.info.get("feasible", False))
    z1 = res.Z1 if (feasible and res.Z1 is not None) else _BIG
    objective = _objective_from_parts(prob_info, z1, z2, z3)
    objective_source = "formula"
    checker_stage = None

    if utils_mod is not None and res.solution is not None:
        try:
            chk = utils_mod.check_feasibility(prob_info, res.solution)
        except Exception as exc:
            chk = {"error": repr(exc)}
        if "error" not in chk:
            checker_stage = chk.get("stage")
            feasible = bool(chk.get("feasible", False))
            if feasible:
                z1 = chk.get("obj1", z1)
                z2 = chk.get("obj2", z2)
                z3 = chk.get("obj3", z3)
                objective = chk.get("objective", _objective_from_parts(prob_info, z1, z2, z3))
                objective_source = "utils.check_feasibility"
            else:
                z1 = _BIG
                objective = float("inf")
                objective_source = "utils.check_feasibility"
        else:
            objective_source = "formula_fallback"
    elif not feasible:
        objective = float("inf")

    sol = Solution(
        bay=list(bay), entry=res.entry, exit_=res.exit_,
        coords=res.coords, orient=res.orient,
        Z1=z1, Z2=z2, Z3=z3, objective=objective,
        solution=res.solution, feasible=feasible,
        forced=len(res.info.get("forced", [])),
    )
    meta = {
        "objective_source": objective_source,
        "checker_stage": checker_stage,
        "phase2_feasible_flag": bool(res.info.get("feasible", False)),
        "phase2_status": res.status,
    }
    return sol, meta


def realize(bay: list, prob_info: dict, pre, phase2cfg=None, deadline=None) -> Solution:
    p1 = init_timing(list(bay), prob_info, pre)
    base_cfg = deepcopy(phase2cfg) if phase2cfg is not None else Phase2Config()
    debug_enabled = _debug_phase2_variants(base_cfg)

    try:
        utils_mod = _load_utils()
    except Exception:
        utils_mod = None

    baseline = None
    baseline_fp = None
    best = None
    best_fp = None
    best_variant_id = None
    best_obj = float("inf")
    debug_rows = []

    for idx, cfg in enumerate(_build_variant_cfgs(base_cfg, debug_enabled)):
        if idx > 0 and deadline is not None and time.perf_counter() >= deadline:
            if debug_enabled:
                _emit_variant_debug("deadline_stop", {
                    "next_variant_id": cfg._variant_id,
                    "contact_exact_top_k": int(getattr(cfg, "contact_exact_top_k", 8)),
                })
            break

        t0 = time.perf_counter()
        res = PlaceAndCrane(prob_info, p1, pre, cfg)
        runtime_ms = int((time.perf_counter() - t0) * 1000)
        sol, meta = _solution_from_result(bay, p1, res, prob_info, utils_mod)
        fingerprint, op_count = _solution_ops_fingerprint(sol.solution)

        if baseline is None:
            baseline = sol
            baseline_fp = fingerprint

        same_as_baseline = baseline_fp == fingerprint
        same_as_best_before = best_fp == fingerprint if best_fp is not None else False

        if sol.feasible and sol.objective < best_obj:
            best = sol
            best_fp = fingerprint
            best_variant_id = cfg._variant_id
            best_obj = sol.objective

        record = {
            "variant_id": cfg._variant_id,
            "variant_kind": cfg._variant_kind,
            "config_object_id": id(cfg),
            "contact_exact_top_k": int(getattr(cfg, "contact_exact_top_k", 8)),
            "runtime_ms": runtime_ms,
            "feasible": bool(sol.feasible),
            "objective": sol.objective,
            "objective_source": meta["objective_source"],
            "checker_stage": meta["checker_stage"],
            "phase2_feasible_flag": meta["phase2_feasible_flag"],
            "phase2_status": meta["phase2_status"],
            "Z1": sol.Z1,
            "forced": sol.forced,
            "operations": op_count,
            "fingerprint": fingerprint,
            "identical_to_baseline": same_as_baseline,
            "identical_to_current_best_before_update": same_as_best_before,
            "placement_calls": cfg._debug_variant_stats.get("placement_calls", 0),
            "zero_feasible_calls": cfg._debug_variant_stats.get("zero_feasible_calls", 0),
            "feasible_candidates_total": cfg._debug_variant_stats.get("feasible_candidates_total", 0),
            "exact_candidates_total": cfg._debug_variant_stats.get("exact_candidates_total", 0),
            "max_feasible_candidates": cfg._debug_variant_stats.get("max_feasible_candidates", 0),
            "truncated_calls": cfg._debug_variant_stats.get("truncated_calls", 0),
        }
        debug_rows.append(record)
        if debug_enabled:
            _emit_variant_debug("variant", record)

    selected = best if best is not None else baseline

    if debug_enabled:
        summary = {
            "selected_variant_id": best_variant_id if best is not None else "baseline_fallback",
            "selected_contact_exact_top_k": (
                next((row["contact_exact_top_k"] for row in debug_rows
                      if row["variant_id"] == best_variant_id), None)
                if best is not None else (
                    debug_rows[0]["contact_exact_top_k"] if debug_rows else None
                )
            ),
            "selected_objective": selected.objective if selected is not None else float("inf"),
            "baseline_objective": baseline.objective if baseline is not None else float("inf"),
            "did_selection_change_from_baseline": bool(
                selected is not None and baseline is not None and
                _solution_ops_fingerprint(selected.solution)[0] != baseline_fp
            ),
            "variants_executed": [row["variant_id"] for row in debug_rows],
        }
        _emit_variant_debug("summary", summary)

    if selected is not None:
        selected._phase2_variant_debug = debug_rows
        return selected

    res = PlaceAndCrane(prob_info, p1, pre, base_cfg)
    sol, _ = _solution_from_result(bay, p1, res, prob_info, utils_mod)
    sol._phase2_variant_debug = debug_rows
    return sol
