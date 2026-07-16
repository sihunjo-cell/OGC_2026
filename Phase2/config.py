"""Phase 2 configuration (이벤트 구동 dispatch 경로 전용)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Phase2Config:
    # -- 이벤트 구동 ATC 디스패처 (Phase2.dispatch) ---------------------------
    atc_kappa: float = 2.0          # ATC 여유 감쇠 (포트폴리오 워커별 로터리)
    atc_alpha: float = 0.0          # 0 = 순수 ATC (면적 항 끔)
    dispatch_cand_cap: int = 12     # (bay, orient)당 정확 게이트에 넘길 셀 수
    dispatch_cand_cap_hi: int = 48  # 큐 길이 >= queue_hi면 확대
    dispatch_queue_hi: int = 20
    scan_incremental: bool = True   # 스캔 공간 국소 무효화 (False = 검증/계측용)
    dispatch_admit_fail_stop: int = 0   # 마지막 admit 후 F회 연속실패 시 패스 종료 (0=끔)
    mask_cache_share: bool = True   # 마스크 캐시 pre 공유 (설계도 간 재사용, 비트동일)
    # 동적 bay: 배정 bay에 못 들고 지각확정(t+P>D)인 블록만 least-util bay로 admit-now.
    # False = 고정-bay (포트폴리오 dyn-off floor 워커용).
    dispatch_dynamic_bay: bool = True
    # ΔF 파편화 항 (FGD, issue/05): 앵커 점수에 "큐 대형 M*의 앵커를 죽이는 비율"
    # 페널티. 0 = 끔(비트동일). flag-off 보관(예산-민감, T>=300 재평가 조건).
    dispatch_fragdelta: float = 0.0
    fragdelta_queue_hi: int = 6     # bay 큐 길이 임계 (이상일 때만 활성)
    fragdelta_q: int = 4            # M* 크기
    fragdelta_horizon: int = 0      # M*에 R<=t+H 미도착 대형 포함 (0=큐만)
    fragdelta_flop_cap: float = 2e9  # 디코드당 FLOP 상한 -- 초과 시 잔여 비활성
    # hull-nestle 회수 (N2, issue/06): mask 패스 전멸 시 0<count<=K 앵커를 count
    # 오름차순 cap개 exact 공간검사 + 기존 크레인 게이트로 admit. 0 = 끔(비트동일).
    dispatch_nestle_k: int = 0
    dispatch_nestle_cap: int = 12
    dispatch_nestle_fast: bool = True    # 판정-동치 numba 가속 (False = 순수 shapely)
    dispatch_nestle_flop_cap: float = 2e9  # 디코드당 FLOP 상한 (fragdelta 캡과 동형)

    # -- 크레인 repair 안전망 (driver) ----------------------------------------
    max_repair_passes: int = 2
    force_retry_phase_b: bool = True    # force_place 전 부분점유 슬롯 재시도
    force_retry_budget: int = 16        # 디코드당 재시도 상한
