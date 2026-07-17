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
    scan_incremental: bool = True   # 스캔 지도 공간 국소 무효화(끄면 작업장 통째 재계산)
    # admission 조기중단: 마지막 admit 후 F회 연속 실패면 남은 큐를 다음 이벤트로 이월.
    # 0 = 끔. 스캔비용의 76~81%가 마지막 admit 이후에 소모되는 꼬리를 자른다.
    dispatch_admit_fail_stop: int = 0
    # 마스크 캐시를 pre에 공유해 설계도(Raster 인스턴스) 간 재사용(비트동일).
    mask_cache_share: bool = True
    # 동적 bay 선택. 블록이 배정 bay에 못 들고 '지금 넣어도 지각(t+P>D)'인 급한 블록이면
    # 다른 eligible bay 중 가장 여유있는(least-util) bay로 admit-now 시도 -- 고정-bay가
    # 못 푸는 no_space(다른 bay엔 자리 있음)를 해소. 여유 블록은 제 bay 대기.
    # False = 고정-bay 동작(포트폴리오의 dyn-off floor 워커가 사용).
    dispatch_dynamic_bay: bool = True
    # Critical-event MPC beam. Greedy remains the default; when enabled, a
    # pressured bay commits one lookahead-selected admission before falling
    # back to the normal greedy loop.
    dispatch_beam: bool = False
    beam_depth: int = 3
    beam_width: int = 8
    beam_top_blocks: int = 4
    beam_top_anchors: int = 4
    beam_trigger_queue: int = 8
    beam_max_expansions: int = 256
    beam_congestion_penalty: float = 200.0
    # Serial SGS worker: select an unscheduled block first, then find its
    # earliest feasible time/bay/placement against the partial schedule.
    dispatch_serial: bool = False
    serial_rule: str = "large_critical"
    serial_top_blocks: int = 6
    serial_top_bays: int = 3
    serial_anchor_cap: int = 8
    serial_time_cap: int = 32
    serial_rank_penalty: float = 1000.0
    serial_area_alpha: float = 0.7
    serial_long_alpha: float = 0.5
    serial_dynamic_bay: bool = True

    # -- 크레인 repair 안전망 (driver) ----------------------------------------
    max_repair_passes: int = 2
    # Phase B: force_place(빈 창) 전 부분점유 슬롯 재시도. budget은 디코드당 상한.
    force_retry_phase_b: bool = True
    force_retry_budget: int = 16
