"""Outer.config -- ALNS tuning parameters."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class OuterConfig:
    # -- O.1 destroy ----------------------------------------------------------
    xi: float = 0.4
    destroy_ops: tuple = ("random", "worst", "related")
    p_worst: float = 6.0
    p_shaw: float = 6.0
    phi: float = 1.0
    chi: float = 1.0
    psi: float = 1.0
    omega: float = 1.0

    # -- O.2 repair -----------------------------------------------------------
    repair_ops: tuple = ("greedy", "regret_k")
    regret_k: int = 3
    repair_noise: float = 0.1
    crowd_weight: float = 6.0
    crowd_eta: float = 0.85

    # -- O.3 adaptive operator scoring ---------------------------------------
    r: float = 0.1
    sigma1: float = 33.0
    sigma2: float = 9.0
    sigma3: float = 13.0
    seg: int = 100

    # -- simulated annealing acceptance --------------------------------------
    w_pct: float = 0.05
    c: float = 0.99975

    # -- stagnation restart ---------------------------------------------------
    restart_stall: int = 0

    # -- O.6 cycle exchange probe --------------------------------------------
    cyclex_stall: int = 0
    cyclex_nodes: int = 24
    cyclex_max_cycles: int = 20000
    cyclex_min_proxy_gain: float = 0.0
    cyclex_neighbor_k: int = 3
    cyclex_pref_nodes: int = 6
    cyclex_realize_k: int = 4
    inbay_stall: int = 0
    inbay_realize_k: int = 4
    inbay_bays: int = 2
    inbay_top: int = 10

    # -- misc -----------------------------------------------------------------
    seed: int = 0
    phase1: object = None
    phase2: object = None