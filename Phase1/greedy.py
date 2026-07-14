"""Phase1.greedy -- FirstFit greedy bay 배정.

블록을 EDD 순(slack↑, due↑)으로, eligible + DFF 통과 bay 중 점수
(w2·Z2증가 + w3·선호 + 혼잡 페널티)를 최소화하는 곳에 배정. bay[i] 반환.
"""

from __future__ import annotations

from . import common
from .dff import dff_lb


def _max_imbalance(load: list, u: list) -> float:
    """max_{j1!=j2} | u_j1*load_j1 - u_j2*load_j2 |  -- floor 안 씌운 Z2 값."""
    m = len(load)
    if m < 2:
        return 0.0
    wl = [u[j] * load[j] for j in range(m)]
    return max(wl) - min(wl)


def firstfit_greedy(prob_info: dict, pre, cfg) -> list:
    blocks = prob_info["blocks"]
    bays = prob_info["bays"]
    n = len(blocks)
    m = len(bays)

    L = [b["workload"] for b in blocks]
    S = [b["bay_preferences"] for b in blocks]
    Smax = pre.Smax
    EST = pre.EST
    P = [b["processing_time"] for b in blocks]
    u = pre.u
    wh = [common.block_wh(pre, i) for i in range(n)]
    exit0 = [EST[i] + P[i] for i in range(n)]

    # load(Z2 proxy)와 선호도(Z3 증가분) 항에 목적함수 가중치를 곱해,
    # 기본 동작이 가중 목적함수를 greedy하게 최소화하도록 한다.
    weights = prob_info.get("weights", {})
    w2 = weights.get("w2", 1.0)
    w3 = weights.get("w3", 1.0)

    w1 = weights.get("w1", 1.0)
    cw = float(getattr(cfg, "crowd_weight", 0.0) or 0.0)
    areas = [common.footprint_area(pre, i) for i in range(n)]
    bay_wh = [bays[j]["width"] * bays[j]["height"] for j in range(m)]
    prof = [[] for _ in range(m)]

    def _crowd(i, j):
        if cw <= 0.0:
            return 0.0
        WH = bay_wh[j]
        ei, xi, ai = EST[i], exit0[i], areas[i]
        times = [ei] + [a for (a, e, ar) in prof[j] if ei <= a < xi]
        peak = 0.0
        for t in times:
            s = ai
            for (a, e, ar) in prof[j]:
                if a <= t < e:
                    s += ar
            if s > peak:
                peak = s
        return cw * w1 * peak / WH

    order = sorted(range(n), key=lambda i: (pre.slack[i], blocks[i]["due_date"]))

    load = [0.0] * m
    bay = [None] * n
    assigned = [[] for _ in range(m)]        # bay별 배정된 블록 id

    for i in order:
        cand = []
        for j in range(m):
            if not common.eligible(pre, i, j):
                continue
            if cfg.use_dff:
                co = [k for k in assigned[j] if common.time_overlap(EST, exit0, i, k)]
                items = [wh[k] for k in co] + [wh[i]]
                if dff_lb(items, bays[j]["width"], bays[j]["height"], cfg.pq_step) > 1.0 + 1e-9:
                    continue                  # 필요조건 위반 -> 후보 제외
            cand.append(j)

        if not cand:
            # 최후 수단: eligible한 것 중 가장 큰 bay (없으면 bay 0 -- 비정상 입력)
            elig = [j for j in range(m) if common.eligible(pre, i, j)]
            cand = [max(elig, key=lambda j: bays[j]["width"] * bays[j]["height"])] if elig else [0]

        def _score(j):
            # 정확한 myopic 목적함수 증가분: 블록 i를 bay j에 넣고 Z2 재계산
            trial = list(load)
            trial[j] += L[i]
            z2_after = _max_imbalance(trial, u)
            return (cfg.alpha_h * w2 * z2_after
                    + cfg.beta_h * w3 * (Smax[i] - S[i][j])
                    + _crowd(i, j))

        j_star = min(cand, key=_score)
        bay[i] = j_star
        load[j_star] += L[i]
        assigned[j_star].append(i)
        prof[j_star].append((EST[i], exit0[i], areas[i]))

    return bay
