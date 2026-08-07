"""(block, orientation)별 기하 테이블(poly/bbox/area/IFP) + lazy NFP 캐시."""

from __future__ import annotations

import math
import os
from typing import Optional

from .config import GEOM_MODE
from .geometry import (bounding_box, polygon_area,
                       nfp_rings, nfp_rings_hybrid, convex_decompose)


# -----------------------------------------------------------------------------
# Lazy NFP 캐시
# -----------------------------------------------------------------------------

class NFPCache:
    """lazy NFP 저장소 (규약 NFP(A,B)=A⊕(−B), 겹침 <=> pos차가 ring 내부)."""

    def __init__(self, poly: list, mode: str = None):
        # poly[i][o][k] -> list[(x, y)] layer k 정점
        self._poly = poly
        self._mode = (mode or GEOM_MODE)
        self._cache: dict = {}       # (i,n,oi,on,ki,kj) -> rings
        self._decomp: dict = {}      # (i,o,k) -> 볼록 분할
        # 엔트리 상한 (초과 시 clear = 비트동일, 무한성장 방지)
        self._cap = int(os.environ.get("OGC_NFP_CAP", "250000"))

    def _layer(self, i: int, o: int, k: int):
        layers = self._poly[i][o]
        if 0 <= k < len(layers):
            return layers[k]
        return None

    def _decompose(self, i: int, o: int, k: int):
        """(i, o, k)의 볼록 분할 (캐시)."""
        key = (i, o, k)
        d = self._decomp.get(key)
        if d is None:
            pts = self._layer(i, o, k)
            d = convex_decompose(pts) if pts else []
            self._decomp[key] = d
        return d

    def crane(self, i: int, n: int, oi: int, on: int, k_i: int, k_j: int) -> list:
        """layer 쌍 (k_i, k_j) 간 NFP ring (없는 layer = [])."""
        key = (i, n, oi, on, k_i, k_j)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        if self._mode == "shapely":
            A = self._layer(i, oi, k_i)
            B = self._layer(n, on, k_j)
            rings = nfp_rings(A, B) if (A is not None and B is not None) else []
        else:  # fast: shapely 레퍼런스와 동일 ring
            A = self._decompose(i, oi, k_i)
            B = self._decompose(n, on, k_j)
            rings = nfp_rings_hybrid(A, B) if (A and B) else []
        if len(self._cache) >= self._cap:
            self._cache.clear()
            self._decomp.clear()
        self._cache[key] = rings
        return rings

    def same_level(self, i: int, n: int, oi: int, on: int, k: int) -> list:
        """같은 layer k 간 NFP."""
        return self.crane(i, n, oi, on, k, k)


# -----------------------------------------------------------------------------
# 0.2 테이블 빌더
# -----------------------------------------------------------------------------

def precompute_geometry(prob_info: dict, geom_mode: Optional[str] = None) -> dict:
    """기하 테이블 dict(poly/bbox/area/IFP/nfp) -- IFP 비면(x_lo>x_hi) 그 bay에 못 들어감."""
    bays = prob_info["bays"]
    blocks = prob_info["blocks"]
    n_blocks = len(blocks)
    n_bays = len(bays)

    poly: list = []
    bbox: list = []
    area: list = []
    ifp: list = []

    for blk in blocks:
        shape = blk["shape"]
        block_poly, block_bbox, block_area, block_ifp = [], [], [], []
        for orient in shape:
            # 원본 정점 유지 (서버가 원본 폴리곤으로 검증 -- invariants 원장)
            layers = [[(float(v[0]), float(v[1])) for v in layer]
                      for layer in orient["layers"] if layer]
            block_poly.append(layers)

            all_verts = [v for layer in layers for v in layer]
            bb = bounding_box(all_verts) if all_verts else (0.0, 0.0, 1.0, 1.0)
            block_bbox.append(bb)

            block_area.append(sum(polygon_area(layer) for layer in layers))

            lx0, ly0, lx1, ly1 = bb
            per_bay = []
            for bj in bays:
                W, H = bj["width"], bj["height"]
                x_lo = math.ceil(-lx0)
                x_hi = math.floor(W - lx1)
                y_lo = math.ceil(-ly0)
                y_hi = math.floor(H - ly1)
                per_bay.append(((x_lo, x_hi), (y_lo, y_hi)))
            block_ifp.append(per_bay)

        poly.append(block_poly)
        bbox.append(block_bbox)
        area.append(block_area)
        ifp.append(block_ifp)

    nfp = NFPCache(poly, mode=geom_mode)

    return {
        "poly": poly,
        "bbox": bbox,
        "area": area,
        "IFP": ifp,
        "nfp": nfp,
        "n_blocks": n_blocks,
        "n_bays": n_bays,
    }
