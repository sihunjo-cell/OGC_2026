"""FirstFit greedy bay 배정 (EDD 순, w2·Z2+w3·선호+혼잡 최소 bay; 마감 후 저비용 완결)."""

from __future__ import annotations

import time

from . import common
from .dff import dff_lb


def firstfit_greedy(prob_info: dict, pre, cfg, deadline=None) -> list:
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

    # 점수 항에 목적함수 가중치 반영
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
        # 마감 후 저비용 완결 (선호 최대 eligible bay -- 근거 = P6 원장)
        if deadline is not None and time.perf_counter() >= deadline:
            elig = [j for j in range(m) if common.eligible(pre, i, j)]
            j_star = (max(elig, key=lambda j: (S[i][j], -j)) if elig
                      else max(range(m), key=lambda j: bays[j]["width"] * bays[j]["height"]))
            bay[i] = j_star
            load[j_star] += L[i]
            assigned[j_star].append(i)
            prof[j_star].append((EST[i], exit0[i], areas[i]))
            continue
        cand = []
        for j in range(m):
            if not common.eligible(pre, i, j):
                continue
            co = [k for k in assigned[j] if common.time_overlap(EST, exit0, i, k)]
            items = [wh[k] for k in co] + [wh[i]]
            if dff_lb(items, bays[j]["width"], bays[j]["height"], cfg.pq_step) > 1.0 + 1e-9:
                continue                      # DFF 필요조건 위반
            cand.append(j)

        if not cand:
            # 최후 수단: eligible 중 최대 bay
            elig = [j for j in range(m) if common.eligible(pre, i, j)]
            cand = [max(elig, key=lambda j: bays[j]["width"] * bays[j]["height"])] if elig else [0]

        def _score(j):
            # myopic 목적함수 증가분
            trial = list(load)
            trial[j] += L[i]
            z2_after = common.max_imbalance(trial, u)
            return (cfg.alpha_h * w2 * z2_after
                    + cfg.beta_h * w3 * (Smax[i] - S[i][j])
                    + _crowd(i, j))

        j_star = min(cand, key=_score)
        bay[i] = j_star
        load[j_star] += L[i]
        assigned[j_star].append(i)
        prof[j_star].append((EST[i], exit0[i], areas[i]))

    return bay
