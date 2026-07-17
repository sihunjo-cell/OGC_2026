"""Outer.config -- ALNS 튜닝 파라미터(destroy/repair/AOS/acceptance)를 한곳에."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class OuterConfig:
    # -- O.1 destroy(제거) ----------------------------------------------------
    xi: float = 0.4                                  # 제거 비율 q = xi*n
    destroy_ops: tuple = ("random", "worst", "related")
    p_worst: float = 6.0                             # worst 제거 결정성 지수(클수록 상위 편향)
    p_shaw: float = 6.0                              # shaw 제거 결정성 지수
    phi: float = 1.0                                 # R_related: 같은 베이 여부
    chi: float = 1.0                                 # R_related: 진입시각 차 |ENTRY_i - ENTRY_j|
    psi: float = 1.0                                 # R_related: 공간 근접도
    omega: float = 1.0                               # R_related: 형상 유사도

    # -- O.2 repair(복구) -----------------------------------------------------
    repair_ops: tuple = ("greedy", "regret_k")
    regret_k: int = 3
    repair_noise: float = 0.1          # 삽입비용 노이즈 (0이면 이웃이 한 점으로 붕괴)
    crowd_weight: float = 6.0          # Z1 인지 혼잡 페널티 배율 (0 = Z1 무시 기준선)
    crowd_eta: float = 0.85            # 용량 비율. over = peak - eta*WH hinge

    # -- O.3 적응적 가중치 AOS ------------------------------------------------
    r: float = 0.1                                   # 반응 계수
    sigma1: float = 33.0                             # 전역 최적 갱신
    sigma2: float = 9.0                              # 개선하여 수용
    sigma3: float = 13.0                             # 악화지만 수용(다양화)
    seg: int = 100                                   # 세그먼트 길이(반복 수)

    # -- 수용 판정(SA) --
    w_pct: float = 0.05                              # 시작 온도: w% 악화를 확률 0.5로 수용
    c: float = 0.99975                               # 기하 냉각 계수

    # -- 정체 재시작 --
    restart_stall: int = 0             # N회 연속 best 미갱신 시 κ-지터 재시작 (0=끔)

    # -- 기타 ------------------------------------------------------------------
    seed: int = 0
    phase1: object = None                            # 초기해용 Phase1Config (None이면 기본값)
    phase2: object = None                            # realize()용 Phase2Config (None이면 기본값)
