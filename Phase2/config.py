"""Phase 2 configuration (이벤트 구동 dispatch 경로 전용)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Phase2Config:
    # -- 이벤트 구동 ATC 디스패처 (Phase2.dispatch) ---------------------------
    atc_kappa: float = 2.0          # ATC 여유 감쇠 (포트폴리오 로터리: 0.5/2/4)
    atc_alpha: float = 0.0          # 0 = 순수 ATC (면적 항 끔)
    dispatch_cand_cap: int = 12     # (bay, orient)당 정확 게이트에 넘길 셀 수
    dispatch_cand_cap_hi: int = 48  # 큐가 밀리면 확대 ("12셀 절벽" 수정)
    dispatch_queue_hi: int = 20     # 확대 발동 큐 길이

    # -- 크레인 repair 안전망 (driver) ----------------------------------------
    max_repair_passes: int = 2
    # Phase B: force_place(빈 창) 전 부분점유 슬롯 재시도. budget은 디코드당 상한.
    force_retry_phase_b: bool = True
    force_retry_budget: int = 16
