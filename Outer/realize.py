"""
Outer.realize -- evaluate a bay assignment as a complete Solution.

The bay assignment is fixed first, then Phase 2 is run to produce a full layout.
When enabled, multiple complete Phase 2 variants are decoded and the final choice
is made by the actual solution objective, not by local placement heuristics.
"""

from __future__ import annotations

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
    topks = getattr(cfg, "phase2_variant_topks", (8, 16, 32))  # FIXME
    out = []
    for value in topks or ():
        try:
            out.append(max(1, int(value)))
        except Exception:
            continue
    return tuple(out)


def _build_variant_cfgs(base_cfg) -> list:
    cfgs = [("base", deepcopy(base_cfg))]
    base_top_k = max(1, int(getattr(base_cfg, "contact_exact_top_k", 8)))
    seen = {base_top_k}
    for top_k in _phase2_variant_topks(base_cfg):
        if top_k in seen:
            continue
        seen.add(top_k)
        cfg = deepcopy(base_cfg)
        cfg.contact_exact_top_k = top_k
        cfgs.append((top_k, cfg))
    return cfgs


def _solution_from_result(bay: list, p1, res, prob_info: dict, utils_mod=None) -> Solution:
    z2 = p1.Z2 if p1.Z2 is not None else 0.0
    z3 = p1.Z3 if p1.Z3 is not None else 0.0
    feasible = bool(res.info.get("feasible", False))
    z1 = res.Z1 if (feasible and res.Z1 is not None) else _BIG
    objective = _objective_from_parts(prob_info, z1, z2, z3)

    if utils_mod is not None and res.solution is not None:
        try:
            chk = utils_mod.check_feasibility(prob_info, res.solution)
        except Exception:
            chk = None
        if chk is not None:
            feasible = bool(chk.get("feasible", False))
            if feasible:
                z1 = chk.get("obj1", z1)
                z2 = chk.get("obj2", z2)
                z3 = chk.get("obj3", z3)
                objective = chk.get("objective", _objective_from_parts(prob_info, z1, z2, z3))
            else:
                z1 = _BIG
                objective = float("inf")
    elif not feasible:
        objective = float("inf")

    return Solution(
        bay=list(bay), entry=res.entry, exit_=res.exit_,
        coords=res.coords, orient=res.orient,
        Z1=z1, Z2=z2, Z3=z3, objective=objective,
        solution=res.solution, feasible=feasible,
        forced=len(res.info.get("forced", [])),
    )


def realize(bay: list, prob_info: dict, pre, phase2cfg=None, deadline=None) -> Solution:
    p1 = init_timing(list(bay), prob_info, pre)
    base_cfg = deepcopy(phase2cfg) if phase2cfg is not None else Phase2Config()

    try:
        utils_mod = _load_utils()
    except Exception:
        utils_mod = None

    baseline = None
    best = None
    best_obj = float("inf")

    for _, cfg in _build_variant_cfgs(base_cfg):
        if deadline is not None and time.time() >= deadline:
            break

        res = PlaceAndCrane(prob_info, p1, pre, cfg)
        sol = _solution_from_result(bay, p1, res, prob_info, utils_mod)

        if baseline is None:
            baseline = sol
        if sol.feasible and sol.objective < best_obj:
            best = sol
            best_obj = sol.objective

    if best is not None:
        return best
    if baseline is not None:
        return baseline

    res = PlaceAndCrane(prob_info, p1, pre, base_cfg)
    return _solution_from_result(bay, p1, res, prob_info, utils_mod)
