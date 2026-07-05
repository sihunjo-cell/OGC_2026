"""
Phase1 -- OGC 2026 bay 배정.

BuildBayAssignment는 목적함수 가중 first-fit greedy와 Init_Timing을 돌려
Phase 2가 바로 쓰는 Phase2.contract.Phase1Output을 반환한다.
"""

from .config import Phase1Config
from .driver import BuildBayAssignment, build_bay_assignment

__all__ = ["Phase1Config", "BuildBayAssignment", "build_bay_assignment"]
