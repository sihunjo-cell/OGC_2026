"""잠정 timing(ENTRY=EST) + Z1/Z2/Z3 계산 (Z2/Z3 = 공식 utils 공식 그대로)."""

from __future__ import annotations

import math

from Phase2.contract import Phase1Output


def init_timing(bay: list, prob_info: dict, pre) -> Phase1Output:
    blocks = prob_info["blocks"]
    n = len(blocks)
    m = pre.n_bays

    P = [b["processing_time"] for b in blocks]
    D = [b["due_date"] for b in blocks]
    L = [b["workload"] for b in blocks]
    S = [b["bay_preferences"] for b in blocks]
    Smax = pre.Smax
    u = pre.u

    entry = list(pre.EST)
    exit_ = [entry[i] + P[i] for i in range(n)]

    # 임시 Z1
    Z1 = sum(max(0, exit_[i] - D[i]) for i in range(n))

    # Z2 (utils 공식)
    load = [0.0] * m
    for i in range(n):
        load[bay[i]] += L[i]
    if m >= 2:
        Z2 = math.floor(max(
            abs(u[j1] * load[j1] - u[j2] * load[j2])
            for j1 in range(m) for j2 in range(m) if j1 != j2
        ))
    else:
        Z2 = 0.0

    # Z3: 선호도 페널티
    Z3 = sum(Smax[i] - S[i][bay[i]] for i in range(n))

    return Phase1Output(bay=bay, entry=entry, exit_=exit_,
                        Z1=Z1, Z2=Z2, Z3=Z3)
