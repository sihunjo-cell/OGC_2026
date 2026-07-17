"""Phase1.driver -- BuildBayAssignment: greedy bay 배정 후 Init_Timing."""

from __future__ import annotations

from .config import Phase1Config
from .greedy import firstfit_greedy
from .timing import init_timing


def build_bay_assignment(prob_info: dict, pre, cfg: Phase1Config = None, deadline=None):
    """Phase1Output 반환 (deadline 초과 시 잔여 저비용 완결)."""
    cfg = cfg or Phase1Config()

    bay = firstfit_greedy(prob_info, pre, cfg, deadline=deadline)
    out = init_timing(bay, prob_info, pre)
    return out, "greedy"


def BuildBayAssignment(prob_info: dict, pre, cfg: Phase1Config = None, deadline=None):
    """Phase1Output만 반환하는 래퍼."""
    out, _ = build_bay_assignment(prob_info, pre, cfg, deadline=deadline)
    return out
