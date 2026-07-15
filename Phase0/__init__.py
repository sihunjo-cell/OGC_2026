"""Phase0 -- OGC 2026 전처리: 상수(0.1), 지오메트리 테이블(0.2)."""

from .config import DP_TOL, EPS
from .constants import precompute_constants
from .geometry_tables import precompute_geometry, NFPCache
from .preprocess import preprocess, PRE

__all__ = [
    "DP_TOL", "EPS",
    "precompute_constants",
    "precompute_geometry", "NFPCache",
    "preprocess", "PRE",
]
