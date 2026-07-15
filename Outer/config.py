"""Outer.config -- ALNS tuning parameters."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class OuterConfig:
    # -- O.1 destroy -----------------------------------------------------------
    xi: float = 0.4
    destroy_ops: tuple = ("random", "worst", "related")
    p_worst: float = 6.0
    p_shaw: float = 6.0
    phi: float = 1.0
    chi: float = 1.0
    psi: float = 1.0
    omega: float = 1.0

    # -- O.2 repair ------------------------------------------------------------
    repair_ops: tuple = ("greedy", "regret_k")
    regret_k: int = 3
    repair_noise: float = 0.1

    # Z1 인지 혼잡 페널티: repair는 지연(Z1, Phase 2에서만 드러남)을 못 보므로
    # 과밀·강제 배정을 피하도록 유도한다. 0이면 Z1 무시 기준선.
    # 6.0 = both-mode(greedy+repair) cw 그리드서치 최적(2026-07-15, 병목 4문제).
    crowd_weight: float = 6.0
    crowd_eta: float = 0.85            # 용량 비율(Phase 1 eta와 동일). over=peak-eta*WH hinge

    # -- O.3 적응적 가중치 AOS ------------------------------------------------
    r: float = 0.1                                   # 반응 계수
    sigma1: float = 33.0                             # 전역 최적 갱신
    sigma2: float = 9.0                              # 개선하여 수용
    sigma3: float = 13.0                             # 악화지만 수용(다양화)
    seg: int = 100                                   # 세그먼트 길이(반복 수)

    # -- O.4 수용 판정(SA) ----------------------------------------------------
    w_pct: float = 0.05                              # 시작 온도: w% 악화를 확률 0.5로 수용
    c: float = 0.99975                               # 기하 냉각 계수

    # -- 기타 ------------------------------------------------------------------
    seed: int = 0
    phase1: object = None
    phase2: object = None
