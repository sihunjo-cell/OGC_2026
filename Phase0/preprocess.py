"""Phase0.preprocess -- 오케스트레이터: 0.1 상수 + 0.2 지오메트리를 PRE 번들로 묶음."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .constants import precompute_constants
from .geometry_tables import precompute_geometry, NFPCache


@dataclass
class PRE:
    """Phase 0 전체 출력(0.1 + 0.2). 접근 편하게 펼쳐 놓음."""

    # -- 0.1 상수 -------------------------------------------------------------
    u: list          # u[j]     bay 면적 가중치 = Abar / (W_j * H_j)
    Smax: list       # Smax[i]  = max_j bay_preferences_i[j]
    EST: list        # EST[i]   = release_time_i
    slack: list      # slack[i] = due_date_i - release_time_i - processing_time_i

    # -- 0.2 지오메트리 테이블 ------------------------------------------------
    poly: list       # poly[i][o][k] -> list[(x, y)]  단순화된 layer-k 정점
    bbox: list       # bbox[i][o]    -> (min_x, min_y, max_x, max_y)
    area: list       # area[i][o]    -> float
    IFP: list        # IFP[i][o][j]  -> ((x_lo, x_hi), (y_lo, y_hi))
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
            u=const["u"], Smax=const["Smax"],
            EST=const["EST"], slack=const["slack"],
            poly=geom["poly"], bbox=geom["bbox"], area=geom["area"],
            IFP=geom["IFP"], nfp=geom["nfp"],
            n_blocks=geom["n_blocks"], n_bays=geom["n_bays"],
        )


def preprocess(prob_info: dict, dp_tol: Optional[float] = None,
               geom_mode: Optional[str] = None) -> PRE:
    """0.1 + 0.2를 돌려서 Phase 1용 PRE 번들 반환. geom_mode로 NFP 백엔드 오버라이드
    (기본값 config.GEOM_MODE / OGC_GEOM).
    """
    return PRE.build(prob_info, dp_tol=dp_tol, geom_mode=geom_mode)
