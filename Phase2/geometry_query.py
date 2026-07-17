"""hot loop용 순수 파이썬 geometry 쿼리 (NFP 조회/reflect memo, 규약 = invariants 원장)."""

from __future__ import annotations

import math
import os
import weakref

import numpy as _np

# numba 가속 (선택; njit 커널은 py 경로와 수치 동일, 부재 시 폴백)
try:
    from numba import njit as _njit
    _HAVE_NUMBA = True
except Exception:                        # pragma: no cover
    _HAVE_NUMBA = False
    def _njit(*a, **k):
        def _wrap(f):
            return f
        return _wrap

_EPS = 1e-9


# -----------------------------------------------------------------------------
# 좌표 변환
# -----------------------------------------------------------------------------

def world_bbox(pre, i: int, o: int, pos: tuple) -> tuple:
    """bay 좌표계 bounding box."""
    x, y = pos
    x0, y0, x1, y1 = pre.bbox[i][o]
    return (x0 + x, y0 + y, x1 + x, y1 + y)


def num_layers(pre, i: int, o: int) -> int:
    return len(pre.poly[i][o])


# -----------------------------------------------------------------------------
# 겹침 프리미티브
# -----------------------------------------------------------------------------

def bbox_overlap(a: tuple, b: tuple) -> bool:
    """엄격 AABB 겹침 (변/코너 공유 = 아님; 공식 utils와 동일 판정)."""
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _in_ring_py(x, y, ring):
    """ray-cast point-in-ring (py 폴백)."""
    n = len(ring)
    inside = False
    j = n - 1
    for k in range(n):
        xi, yi = ring[k]
        xj, yj = ring[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = k
    return inside


@_njit(cache=True)
def _in_ring_arr(x, y, ring):             # pragma: no cover (njit)
    """njit ray-cast (_in_ring_py와 수치 동일)."""
    n = ring.shape[0]
    inside = False
    j = n - 1
    for k in range(n):
        xi = ring[k, 0]; yi = ring[k, 1]
        xj = ring[j, 0]; yj = ring[j, 1]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = k
    return inside


def _ext_arr(r):
    """exterior 배열 lazy 캐시."""
    a = r.get("_ext_arr")
    if a is None:
        a = r["_ext_arr"] = _np.asarray(r["ext"], _np.float64)
    return a


def _hole_arrs(r):
    a = r.get("_hole_arrs")
    if a is None:
        a = r["_hole_arrs"] = [_np.asarray(h, _np.float64) for h in r["holes"]]
    return a


def point_in_rings(px: float, py: float, rings: list) -> bool:
    """점이 ring 집합 내부에 엄격히 있는가 (변 위 = 바깥)."""
    if _HAVE_NUMBA:
        for r in rings:
            if _in_ring_arr(px, py, _ext_arr(r)):
                if not any(_in_ring_arr(px, py, h) for h in _hole_arrs(r)):
                    return True
        return False
    for r in rings:
        if _in_ring_py(px, py, r["ext"]) and not any(_in_ring_py(px, py, h) for h in r["holes"]):
            return True
    return False


def reflect_rings(rings: list) -> list:
    """원점 대칭 (NFP(a,b) -> NFP(b,a))."""
    out = []
    for r in rings:
        r2 = {
            "ext": [(-x, -y) for (x, y) in r["ext"]],
            "holes": [[(-x, -y) for (x, y) in h] for h in r["holes"]],
        }
        if _HAVE_NUMBA:
            r2["_ext_arr"] = -_ext_arr(r)                 # 원본 한 번 캐시 후 부호 반전
            r2["_hole_arrs"] = [-h for h in _hole_arrs(r)]
        out.append(r2)
    return out


# reflect NFP memo (read-only 공유, WeakKey로 PRE 수명 동조 -- 규약 = invariants 원장)
_REL_MEMO: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()
_REL_CAP = int(os.environ.get("OGC_NFP_CAP", "250000"))


def _rel_memo(nfp):
    m = _REL_MEMO.get(nfp)
    if m is None:
        m = _REL_MEMO[nfp] = {}
    return m


def _memo_put(memo, key, rings):
    # 상한 초과 시 clear (비트동일)
    if len(memo) >= _REL_CAP:
        memo.clear()
    memo[key] = rings


def relative_nfp(pre, moving: int, fixed: int, o_m: int, o_f: int, k: int) -> list:
    """layer k의 상대 NFP (겹침 <=> pos차가 내부; 정렬쌍 캐시 + reflect)."""
    if fixed < moving:
        return pre.nfp.same_level(fixed, moving, o_f, o_m, k)
    memo = _rel_memo(pre.nfp)
    key = (moving, fixed, o_m, o_f, k)
    rings = memo.get(key)
    if rings is None:
        rings = reflect_rings(pre.nfp.same_level(moving, fixed, o_m, o_f, k))
        _memo_put(memo, key, rings)
    return rings


def relative_nfp_crane(pre, moving: int, fixed: int, o_m: int, o_f: int,
                       k_m: int, k_f: int) -> list:
    """crane NFP (layer 쌍 독립; 정렬쌍 캐시 + reflect)."""
    if fixed < moving:
        return pre.nfp.crane(fixed, moving, o_f, o_m, k_f, k_m)
    memo = _rel_memo(pre.nfp)
    key = ("c", moving, fixed, o_m, o_f, k_m, k_f)
    rings = memo.get(key)
    if rings is None:
        rings = reflect_rings(pre.nfp.crane(moving, fixed, o_m, o_f, k_m, k_f))
        _memo_put(memo, key, rings)
    return rings


# -- bbox 헬퍼 --

def centroid_of_bbox(bb: tuple) -> tuple:
    return ((bb[0] + bb[2]) * 0.5, (bb[1] + bb[3]) * 0.5)


def bay_diagonal(prob_info, j: int) -> float:
    b = prob_info["bays"][j]
    return math.hypot(b["width"], b["height"])
