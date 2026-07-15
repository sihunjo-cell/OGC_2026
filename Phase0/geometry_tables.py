"""Phase0.geometry_tables -- 0.2단계: (block, orientation)별 테이블(poly, bbox,
area, IFP), 동시 존재 쌍 집합, lazy No-Fit-Polygon 캐시."""

from __future__ import annotations

import math
from typing import Optional

from .config import DP_TOL, GEOM_MODE
from .geometry import (simplify_layer, bounding_box, polygon_area,
                       nfp_rings, nfp_pieces, nfp_rings_hybrid, convex_decompose)


# -----------------------------------------------------------------------------
# Lazy NFP 캐시
# -----------------------------------------------------------------------------

class NFPCache:
    """Lazy 메모이즈 No-Fit-Polygon 저장소 (ring은 첫 요청 시 계산).

    캐시 키에 양쪽 orientation 포함(NFP는 orientation 의존).
    규약: NFP(A,B) = A (+) (-B), A=block i, B=block n. footprint 겹침
    <=> (pos_n - pos_i)가 반환 ring 내부.
    """

    def __init__(self, poly: list, mode: str = None):
        # poly[i][o][k] -> list[(x, y)] layer k 정점
        self._poly = poly
        self._mode = (mode or GEOM_MODE)
        self._cache: dict = {}       # (i,n,oi,on,ki,kj) -> pieces/rings
        self._decomp: dict = {}      # (i,o,k) -> 볼록 분할 (fast/pieces 경로)

    def _layer(self, i: int, o: int, k: int):
        layers = self._poly[i][o]
        if 0 <= k < len(layers):
            return layers[k]
        return None

    def _decompose(self, i: int, o: int, k: int):
        """(block, orientation, layer) 하나의 볼록 분할 (캐시됨)."""
        key = (i, o, k)
        d = self._decomp.get(key)
        if d is None:
            pts = self._layer(i, o, k)
            d = convex_decompose(pts) if pts else []
            self._decomp[key] = d
        return d

    def crane(self, i: int, n: int, oi: int, on: int, k_i: int, k_j: int) -> list:
        """block i(orient oi)의 layer k_i와 block n(orient on)의 layer k_j 간 NFP.
        순수 정점 ring 반환. 한쪽 layer라도 없으면 []."""
        key = (i, n, oi, on, k_i, k_j)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        if self._mode == "shapely":
            A = self._layer(i, oi, k_i)
            B = self._layer(n, on, k_j)
            rings = nfp_rings(A, B) if (A is not None and B is not None) else []
        elif self._mode == "pieces":
            # union 없는 볼록 조각 (실험 전용, 목적함수 바뀔 수 있음)
            A = self._decompose(i, oi, k_i)
            B = self._decompose(n, on, k_j)
            rings = nfp_pieces(A, B) if (A and B) else []
        else:  # "fast": 순수 파이썬 Minkowski + shapely union (shapely와 동일 ring)
            A = self._decompose(i, oi, k_i)
            B = self._decompose(n, on, k_j)
            rings = nfp_rings_hybrid(A, B) if (A and B) else []
        self._cache[key] = rings
        return rings

    def same_level(self, i: int, n: int, oi: int, on: int, k: int) -> list:
        """두 block의 layer k 간 NFP (같은 높이 공간 충돌)."""
        return self.crane(i, n, oi, on, k, k)


# -----------------------------------------------------------------------------
# 0.2 테이블 빌더
# -----------------------------------------------------------------------------

def precompute_geometry(prob_info: dict, dp_tol: Optional[float] = None,
                        geom_mode: Optional[str] = None) -> dict:
    """(block, orientation)별 지오메트리 + IFP 테이블 + lazy NFP 캐시(dp_tol 기본 config.DP_TOL).

    반환 dict:
      poly[i][o][k] -> [(x,y)] layer 정점; bbox[i][o]; area[i][o] (layer 합)
      IFP[i][o][j]  -> ((x_lo,x_hi),(y_lo,y_hi)) 정수 기준점 범위. x_lo > x_hi
                       (또는 y_lo > y_hi)면 block이 bay j에 안 들어감.
      CO            -> [R,D] 창이 겹치는 {(i,n), i<n}; co_adj[i].
      nfp           -> lazy NFPCache; n_blocks, n_bays.
    """
    if dp_tol is None:
        dp_tol = DP_TOL

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
            layers = [simplify_layer(layer, dp_tol) for layer in orient["layers"] if layer]
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

    # 동시 존재 후보 쌍: [R_i,D_i]와 [R_n,D_n] 창이 겹침(양끝 포함). 동시 존재를
    # 보장하진 않는 휴리스틱 사전 필터. 빠진 쌍은 NFPCache가 필요 시 계산.
    R = [blk["release_time"] for blk in blocks]
    D = [blk["due_date"] for blk in blocks]
    CO: set = set()
    co_adj: list = [set() for _ in range(n_blocks)]
    for i in range(n_blocks):
        Ri, Di = R[i], D[i]
        for n in range(i + 1, n_blocks):
            if Ri <= D[n] and R[n] <= Di:      # 창이 겹침(양끝 포함)
                CO.add((i, n))
                co_adj[i].add(n)
                co_adj[n].add(i)

    nfp = NFPCache(poly, mode=geom_mode)

    return {
        "poly": poly,
        "bbox": bbox,
        "area": area,
        "IFP": ifp,
        "CO": CO,
        "co_adj": co_adj,
        "nfp": nfp,
        "n_blocks": n_blocks,
        "n_bays": n_bays,
    }
