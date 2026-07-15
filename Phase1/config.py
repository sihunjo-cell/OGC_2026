"""Phase1.config -- bay 배정 greedy와 DFF 게이트 튜닝 파라미터."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Phase1Config:
    use_dff: bool = True              # DFF 필요조건 게이트 on/off
    # -- greedy 점수 가중치 (w2·Z2 + w3·선호 + 혼잡) ----------------------------
    alpha_h: float = 1.0              # Z2(load 불균형) 항 배율
    beta_h: float = 1.0               # 선호도 페널티 항 배율
    crowd_weight: float = 6.0         # 혼잡 페널티 배율(peak 점유율 비례, greedy+repair 공통)
    # -- DFF 그리드 -----------------------------------------------------------
    pq_step: float = 0.1              # (0, 0.5] 위 (p, q) 그리드 간격
