"""
Outer.realize -- bay 배정을 완전한 Solution으로 평가.

bay 배정을 고정하고 Phase 2(이벤트 구동 dispatch 구성 + 크레인 repair)를 한 번
돌려 완전한 레이아웃과 목적함수를 산출한다. 공식 utils 체커가 있으면 그 값으로
목적함수를 인증(제출 서버와 일치), 없으면 내부 공식으로 폴백한다.
"""

from __future__ import annotations

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

    # 제출 서버와 값을 맞추려고, 가능하면 공식 체커의 목적함수로 인증한다.
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

    return Solution(
        bay=list(bay), entry=res.entry, exit_=res.exit_,
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
    return _solution_from_result(bay, p1, res, prob_info, utils_mod)
