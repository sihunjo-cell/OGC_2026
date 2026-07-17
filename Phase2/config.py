"""Phase 2 configuration."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Phase2Config:
    # Event-driven ATC dispatcher.
    atc_kappa: float = 2.0
    atc_alpha: float = 0.0
    dispatch_cand_cap: int = 12
    dispatch_cand_cap_hi: int = 48
    dispatch_queue_hi: int = 20
    scan_incremental: bool = True
    dispatch_admit_fail_stop: int = 0
    mask_cache_share: bool = True
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

    # Fragmentation delta scoring (dgddgd314). Off by default.
    dispatch_fragdelta: float = 0.0
    fragdelta_queue_hi: int = 6
    fragdelta_q: int = 4
    fragdelta_horizon: int = 0
    fragdelta_flop_cap: float = 2e9

    # Hull nestle recovery (dgddgd314). Off by default unless portfolio enables it.
    dispatch_nestle_k: int = 0
    dispatch_nestle_cap: int = 12
    dispatch_nestle_fast: bool = True
    dispatch_nestle_flop_cap: float = 2e9

    # Crane repair safety net.
    max_repair_passes: int = 2
    force_retry_phase_b: bool = True
    force_retry_budget: int = 16
