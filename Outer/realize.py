"""
Outer.realize -- 베이 배정을 완전한 Solution으로 평가:
    bay --init_timing--> Phase1Output --PlaceAndCrane--> 배치 + 실현 Z1.
`bay`에 대해 결정적이고, 목적함수는 utils 목적함수와 정확히 일치.
"""

from __future__ import annotations

from Phase1.timing import init_timing
from Phase2 import PlaceAndCrane
from .state import Solution

_BIG = 1e18


def realize(bay: list, prob_info: dict, pre, phase2cfg=None) -> Solution:
    p1 = init_timing(list(bay), prob_info, pre)
    res = PlaceAndCrane(prob_info, p1, pre, phase2cfg)

    w = prob_info.get("weights", {})
    w1, w2, w3 = w.get("w1", 1.0), w.get("w2", 1.0), w.get("w3", 1.0)

    feasible = bool(res.info.get("feasible", False))
    Z1 = res.Z1 if (feasible and res.Z1 is not None) else _BIG
    objective = w1 * Z1 + w2 * p1.Z2 + w3 * p1.Z3

    return Solution(
        bay=list(bay), entry=res.entry, exit_=res.exit_,
        coords=res.coords, orient=res.orient,
        Z1=Z1, Z2=p1.Z2, Z3=p1.Z3, objective=objective,
        solution=res.solution, feasible=feasible,
        forced=len(res.info.get("forced", [])),
    )
