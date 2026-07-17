"""bay 배정 -> Phase 2 실행 -> 공식 인증 목적함수의 Solution."""

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
    return w.get("w1", 1.0) * z1 + w.get("w2", 1.0) * z2 + w.get("w3", 1.0) * z3


def _solution_from_result(bay: list, p1, res, prob_info: dict, utils_mod=None) -> Solution:
    z2 = p1.Z2 if p1.Z2 is not None else 0.0
    z3 = p1.Z3 if p1.Z3 is not None else 0.0
    feasible = bool(res.info.get("feasible", False))
    z1 = res.Z1 if (feasible and res.Z1 is not None) else _BIG
    objective = _objective_from_parts(prob_info, z1, z2, z3)

    # 가능하면 공식 체커 값으로 인증
    if utils_mod is not None and res.solution is not None:
        try:
            chk = utils_mod.check_feasibility(prob_info, res.solution)
        except Exception as exc:
            chk = {"error": repr(exc)}
        if "error" not in chk:
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

    # 실현 bay를 ALNS로 되먹임 (탐색 붕괴 방지 -- 근거 = dynamic-bay 원장)
    rbay = res.bay if getattr(res, "bay", None) is not None else bay
    return Solution(
        bay=list(rbay), entry=res.entry, exit_=res.exit_,
        coords=res.coords, orient=res.orient,
        Z1=z1, Z2=z2, Z3=z3, objective=objective,
        solution=res.solution, feasible=feasible,
        forced=len(res.info.get("forced", [])),
        phase2_info=deepcopy(getattr(res, "info", {}) or {}),
    )


def realize(bay: list, prob_info: dict, pre, phase2cfg=None, deadline=None) -> Solution:
    p1 = init_timing(list(bay), prob_info, pre)
    cfg = phase2cfg if phase2cfg is not None else Phase2Config()
    try:
        utils_mod = _load_utils()
    except Exception:
        utils_mod = None
    res = PlaceAndCrane(prob_info, p1, pre, cfg, deadline=deadline)
    # 마감 후 공식 인증 생략 (미인증 = inf 처리, 근거 = P6 원장)
    if deadline is not None and time.perf_counter() >= deadline:
        utils_mod = None
    return _solution_from_result(bay, p1, res, prob_info, utils_mod)
