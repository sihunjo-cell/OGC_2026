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
    scan_morph: bool = True         # 행-gap/행-prefix 스캔 커널 (판정동치 가속; False = 검증용)
    dispatch_admit_fail_stop: int = 0   # 마지막 admit 후 F회 연속실패 시 패스 종료 (0=끔)
    mask_cache_share: bool = True   # 마스크 캐시 pre 공유 (설계도 간 재사용, 비트동일)
    # 동적 bay: 지각확정 블록만 least-util 타 bay로 admit-now (False = 고정-bay floor용)
    dispatch_dynamic_bay: bool = True
    # ΔF 파편화 항: 앵커 점수에 미래-앵커 살해 페널티 (0 = 끔; 배선 근거 = fgd 원장)
    dispatch_fragdelta: float = 0.0
    fragdelta_queue_hi: int = 6     # bay 큐 길이 임계 (이상일 때만 활성)
    fragdelta_q: int = 4            # M* 크기
    fragdelta_flop_cap: float = 2e9  # 디코드당 FLOP 상한 -- 초과 시 잔여 비활성
    # ΔF 형성기 게이트: bay 밀도 >= 값이면 항 비활성 (0 = 게이트 없음)
    fragdelta_dens_hi: float = 0.0
    # hull-nestle 회수: mask 전멸 시 count<=K 앵커 cap개 정밀 재검사 (0 = 끔; 배선값은 portfolio)
    dispatch_nestle_k: int = 0
    dispatch_nestle_cap: int = 32
    dispatch_nestle_fast: bool = True    # 판정-동치 numba 가속 (False = 순수 shapely)
    dispatch_nestle_flop_cap: float = 2e10  # 디코드당 FLOP 상한 (fragdelta 캡과 동형)
    # -- 크레인 repair 안전망 (driver) ----------------------------------------
    max_repair_passes: int = 2
    force_retry_phase_b: bool = True    # force_place 전 부분점유 슬롯 재시도
    force_retry_budget: int = 16        # 디코드당 재시도 상한
