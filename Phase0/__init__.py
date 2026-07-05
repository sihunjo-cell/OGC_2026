"""Phase0 -- OGC 2026 전처리: 상수(0.1), 지오메트리 테이블(0.2), clique(0.3).

    pre = preprocess(prob_info)                # 0.1 + 0.2
    cliques = pre.cliques(entry, exit_, bay)   # 0.3, Phase 1 타이밍 이후
"""

from .config import DP_TOL, EPS
from .constants import precompute_constants
from .geometry_tables import precompute_geometry, NFPCache
from .cliques import precompute_cliques, _interval_maximal_cliques
from .preprocess import preprocess, PRE

__all__ = [
    "DP_TOL", "EPS",
    "precompute_constants",
    "precompute_geometry", "NFPCache",
    "precompute_cliques", "_interval_maximal_cliques",
    "preprocess", "PRE",
]
