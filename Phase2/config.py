"""Phase 2 configuration."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Phase2Config:
    phase2_variant_topks: tuple = (8, 16, 32)  # FIXME

    # Strategy toggles
    improve_mode: str = "off"           # "off" | "jostle_2exchange"
    forcing_mode: str = "empty_bay"     # "empty_bay" | "earliest_slot"

    # Placement order
    order_mode: str = "area"            # "area" | "mst"
    lam1: float = 1.0

    # Placement heuristic weights
    w_ct: float = 1.0
    w_cn: float = 0.01
    w_tp: float = 0.1
    w_pm: float = 1.0
    K: float = 4.0
    contact_exact_top_k: int = 8

    # Improve
    improve_rounds: int = 2

    # Repair
    max_repair_passes: int = 2
