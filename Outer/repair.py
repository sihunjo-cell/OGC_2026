"""
Outer.repair -- 제거된 블록을 빠른 삽입 비용으로 재배정(Phase 2 없이):
    cost(i, j) = w2 * Z2_after(i->j) + w3 * (Smax_i - S_ij) + crowd(i, j)
crowd(i, j)는 혼잡을 피하도록 유도하는 Z1 인지 소프트 면적-용량 페널티
(0이면 Z1 무시 기준선).
  greedy  : 삽입 비용이 가장 작은 블록을 삽입.
  regret_k: k개 최선 베이에 대한 후회가 가장 큰 블록을 삽입.
"""

from __future__ import annotations

from Phase1.common import eligible, footprint_area
from .objective import loads_from_bay, z2_raw


def _eligible_bays(pre, i, m):
    return [j for j in range(m) if eligible(pre, i, j)]


def _areas(pre):
    """PRE 객체에 캐싱한 풋프린트 면적(한 번만 계산)."""
    a = getattr(pre, "_crowd_areas", None)
    if a is None:
        a = [footprint_area(pre, i) for i in range(pre.n_blocks)]
        object.__setattr__(pre, "_crowd_areas", a)
    return a


def repair(partial_bay: list, D, op: str, cfg, prob_info: dict, pre, rng) -> list:
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
    # 혼잡 페널티를 매 repair마다 적용(w1로 스케일, w3*선호와 경쟁). 별도
    # 연산자로 두면 반복이 적은 환경에서 효과가 희석돼 이렇게 통합함
    cw = cfg.crowd_weight
    eta = cfg.crowd_eta
    op_type = "regret_k" if "regret" in op else "greedy"

    bay = list(partial_bay)
    loads = loads_from_bay(bay, L, m)
    remaining = set(D)

    # 현재 배정된(유지+삽입) 블록의 베이별 시간-면적 프로파일:
    # prof[j] = (entry, exit, area) 목록, entry = EST_k, exit = EST_k + P_k.
    prof = [[] for _ in range(m)]
    for k, j in enumerate(bay):
        if j is not None:
            prof[j].append((EST[k], EST[k] + P[k], areas[k]))

    def crowd_pen(i, j):
        if cw <= 0:
            return 0.0
        WH = bays[j]["width"] * bays[j]["height"]
        ei, xi, ai = EST[i], EST[i] + P[i], areas[i]
        # i가 머무는 동안의 최대 동시 점유 면적: i의 진입시각과 i의 윈도 안에
        # 드는 다른 블록의 진입시각만 확인(혼잡은 그 지점에서만 바뀜)
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

    # 노이즈 진폭을 블록의 최대 선호 편차에 맞춰 스케일 -- 교란이 실제
    # 삽입 비용 격차와 비슷한 크기가 되도록
    noise_amp = cfg.repair_noise * max(w3 * 100.0, w2 * 10.0, 1.0)

    def sorted_costs(i):
        out = []
        for j in _eligible_bays(pre, i, m):
            trial = list(loads)
            trial[j] += L[i]
            cost = w2 * z2_raw(trial, u) + w3 * (Smax[i] - S[i][j]) + crowd_pen(i, j)
            if noise_amp > 0:
                cost += rng.uniform(-noise_amp, noise_amp)
            out.append((cost, j))
        out.sort()
        return out

    while remaining:
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

    # 남은 블록(적격 베이 없음 -- 비정상): 가장 큰 베이에 넣음
    for i in remaining:
        elig = _eligible_bays(pre, i, m) or [0]
        bay[i] = max(elig, key=lambda j: bays[j]["width"] * bays[j]["height"])

    return bay


REPAIR_OPS = ("greedy", "regret_k")
