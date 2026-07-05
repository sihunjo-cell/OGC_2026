"""Phase0.preprocess -- 오케스트레이터.

배치와 무관한 전처리(0.1 상수 + 0.2 지오메트리)를 돌려서 Phase 1용 PRE 객체로
묶음. 0.3(clique)은 Phase 1의 ENTRY/EXIT/bay가 필요해서 PRE.cliques(...)로 미룸.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .constants import precompute_constants
from .geometry_tables import precompute_geometry, NFPCache
from .cliques import precompute_cliques


@dataclass
class PRE:
    """Phase 0 전체 출력(0.1 + 0.2). 접근 편하게 펼쳐 놓음."""

    # -- 0.1 상수 -------------------------------------------------------------
    u: list          # u[j]     bay 면적 가중치 = Abar / (W_j * H_j)
    Abar: float      #          평균 bay 면적
    Smax: list       # Smax[i]  = max_j bay_preferences_i[j]
    EST: list        # EST[i]   = release_time_i
    LST0: list       # LST0[i]  = due_date_i - processing_time_i
    slack: list      # slack[i] = due_date_i - release_time_i - processing_time_i

    # -- 0.2 지오메트리 테이블 ------------------------------------------------
    poly: list       # poly[i][o][k] -> list[(x, y)]  단순화된 layer-k 정점
    bbox: list       # bbox[i][o]    -> (min_x, min_y, max_x, max_y)
    area: list       # area[i][o]    -> float
    IFP: list        # IFP[i][o][j]  -> ((x_lo, x_hi), (y_lo, y_hi))
    CO: set          # {(i, n)}  동시 존재 후보 쌍 (i < n)
    co_adj: list     # co_adj[i] -> CO에서 i의 파트너 집합
    nfp: NFPCache    # lazy same-level / crane NFP 저장소

    # -- 메타 -----------------------------------------------------------------
    n_blocks: int
    n_bays: int

    @classmethod
    def build(cls, prob_info: dict, dp_tol: Optional[float] = None,
              geom_mode: Optional[str] = None) -> "PRE":
        const = precompute_constants(prob_info)
        geom = precompute_geometry(prob_info, dp_tol=dp_tol, geom_mode=geom_mode)
        return cls(
            u=const["u"], Abar=const["Abar"], Smax=const["Smax"],
            EST=const["EST"], LST0=const["LST0"], slack=const["slack"],
            poly=geom["poly"], bbox=geom["bbox"], area=geom["area"],
            IFP=geom["IFP"], CO=geom["CO"], co_adj=geom["co_adj"], nfp=geom["nfp"],
            n_blocks=geom["n_blocks"], n_bays=geom["n_bays"],
        )

    def cliques(self, entry: list, exit_: list, bay: list) -> list:
        """0.3단계 -- Phase 1의 entry/exit_/bay 출력으로 bay별 maximal clique 계산."""
        return precompute_cliques(entry, exit_, bay, self.n_bays)


def preprocess(prob_info: dict, dp_tol: Optional[float] = None,
               geom_mode: Optional[str] = None) -> PRE:
    """0.1 + 0.2를 돌려서 Phase 1용 PRE 번들 반환. geom_mode로 NFP 백엔드 오버라이드
    (기본값 config.GEOM_MODE / OGC_GEOM). 0.3은 pre.cliques(...) 호출.
    """
    return PRE.build(prob_info, dp_tol=dp_tol, geom_mode=geom_mode)
