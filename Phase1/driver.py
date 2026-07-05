"""Phase1.driver -- BuildBayAssignment: greedy bay 배정 후 Init_Timing."""

from __future__ import annotations

from .config import Phase1Config
from .greedy import firstfit_greedy
from .timing import init_timing


def build_bay_assignment(prob_info: dict, pre, cfg: Phase1Config = None):
    """Phase1Output(bay, entry, exit_, cliques, Z1, Z2, Z3) 반환."""
    cfg = cfg or Phase1Config()

    bay = firstfit_greedy(prob_info, pre, cfg)
    out = init_timing(bay, prob_info, pre, cfg)
    return out, "greedy"


def BuildBayAssignment(prob_info: dict, pre, cfg: Phase1Config = None):
    """backend 태그를 떼고 Phase1Output만 반환하는 래퍼."""
    out, _ = build_bay_assignment(prob_info, pre, cfg)
    return out
