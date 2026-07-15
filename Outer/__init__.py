"""
Outer -- 베이 배정 공간을 도는 ALNS + N개 병렬 포트폴리오(best-of-N).

ALNS는 베이 배정 공간을 탐색하고, 각 후보는 realize()가
Phase 0 -> 1 -> 2 전체 파이프라인으로 평가한다.
"""

from .config import OuterConfig
from .state import Solution
from .realize import realize
from .alns import alns

__all__ = ["OuterConfig", "Solution", "realize", "alns"]
