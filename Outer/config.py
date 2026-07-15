"""Outer.config -- ALNS tuning parameters."""

from __future__ import annotations

from dataclasses import dataclass, field


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

    # Soft congestion penalty used by bay-level repair.
    crowd_weight: float = 1.0
    crowd_eta: float = 0.85

    # -- O.3 adaptive operator selection --------------------------------------
    r: float = 0.1
    sigma1: float = 33.0
    sigma2: float = 9.0
    sigma3: float = 13.0
    seg: int = 100

    # -- O.4 simulated annealing acceptance -----------------------------------
    w_pct: float = 0.05
    c: float = 0.99975

    # -- O.5 intra-bay local repair -------------------------------------------
    intrabay_enabled: bool = False
    intrabay_max_targets: int = 6
    intrabay_max_moves: int = 2
    intrabay_cand_cap: int = 24
    intrabay_min_gain: float = 1e-6

    # -- Misc ------------------------------------------------------------------
    seed: int = 0
    phase1: object = None
    phase2: object = None
