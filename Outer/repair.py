"""제거 블록 재배정: cost = w2·Z2 + w3·선호 + 혼잡 (greedy / regret_k)."""

from __future__ import annotations

import time

from Phase1.common import eligible, footprint_area, max_imbalance
from .objective import loads_from_bay


def _eligible_bays(pre, i, m):
    return [j for j in range(m) if eligible(pre, i, j)]


def _areas(pre):
    """풋프린트 면적 (PRE에 1회 캐시)."""
    a = getattr(pre, "_crowd_areas", None)
    if a is None:
        a = [footprint_area(pre, i) for i in range(pre.n_blocks)]
        object.__setattr__(pre, "_crowd_areas", a)
    return a


def repair(partial_bay: list, D, op: str, cfg, prob_info: dict, pre, rng, deadline=None) -> list:
    blocks = prob_info["blocks"]
    bays = prob_info["bays"]
    m = pre.n_bays
    L = [b["workload"] for b in blocks]
    S = [b["bay_preferences"] for b in blocks]
    P = [b["processing_time"] for b in blocks]
    Smax = pre.Smax
    EST = pre.EST
    u = pre.u
    w = prob_info.get("weights", {})
    w1, w2, w3 = w.get("w1", 1.0), w.get("w2", 1.0), w.get("w3", 1.0)
    areas = _areas(pre)
    # 혼잡 페널티 (w1 스케일)
    cw = cfg.crowd_weight
    eta = cfg.crowd_eta
    op_type = "regret_k" if "regret" in op else "greedy"

    bay = list(partial_bay)
    loads = loads_from_bay(bay, L, m)
    remaining = set(D)

    # bay별 시간-면적 프로파일 prof[j] = [(entry, exit, area)]
    prof = [[] for _ in range(m)]
    for k, j in enumerate(bay):
        if j is not None:
            prof[j].append((EST[k], EST[k] + P[k], areas[k]))

    def crowd_pen(i, j):
        if cw <= 0:
            return 0.0
        WH = bays[j]["width"] * bays[j]["height"]
        ei, xi, ai = EST[i], EST[i] + P[i], areas[i]
        # peak = 진입시각 지점만 확인 (혼잡은 그때만 변함)
        times = [ei] + [a for (a, e, ar) in prof[j] if ei <= a < xi]
        peak = 0.0
        for t in times:
            s = ai
            for (a, e, ar) in prof[j]:
                if a <= t < e:
                    s += ar
            if s > peak:
                peak = s
        over = peak - eta * WH
        return (cw * w1 * over / WH) if over > 0.0 else 0.0

    # 노이즈 진폭 스케일 (근거 = invariants 원장)
    noise_amp = cfg.repair_noise * max(w3 * 100.0, w2 * 10.0, 1.0)

    def sorted_costs(i):
        out = []
        for j in _eligible_bays(pre, i, m):
            trial = list(loads)
            trial[j] += L[i]
            cost = w2 * max_imbalance(trial, u) + w3 * (Smax[i] - S[i][j]) + crowd_pen(i, j)
            if noise_amp > 0:
                cost += rng.uniform(-noise_amp, noise_amp)
            out.append((cost, j))
        out.sort()
        return out

    while remaining:
        # 마감 후 중단 (잔여는 fallback 완결)
        if deadline is not None and time.perf_counter() >= deadline:
            break
        best = {i: sorted_costs(i) for i in remaining}
        best = {i: cs for i, cs in best.items() if cs}      # 배치 불가 제거(발생하면 안 됨)
        if not best:
            break

        if op_type == "greedy":
            i_star = min(best, key=lambda i: best[i][0][0])
        else:  # regret_k 분기
            def regret(i):
                cs = best[i]
                k = min(cfg.regret_k, len(cs))
                return sum(cs[l][0] - cs[0][0] for l in range(k))
            i_star = max(best, key=regret)

        j_star = best[i_star][0][1]
        bay[i_star] = j_star
        loads[j_star] += L[i_star]
        prof[j_star].append((EST[i_star], EST[i_star] + P[i_star], areas[i_star]))
        remaining.discard(i_star)

    # 잔여 fallback: 최대 bay
    for i in remaining:
        elig = _eligible_bays(pre, i, m) or [0]
        bay[i] = max(elig, key=lambda j: bays[j]["width"] * bays[j]["height"])

    return bay
