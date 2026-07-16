"""Phase2.geometry_query -- 탐색 hot loop용 순수 파이썬 geometry 쿼리.

Phase 0이 미리 계산해 둔 순수 꼭짓점 ring/테이블(PRE.nfp, PRE.poly, PRE.bbox)을
shapely 없이 사용. 좌표 변환, AABB/point-in-ring 겹침, NFP reflection +
relative-NFP 캐시 조회를 다룬다."""

from __future__ import annotations

import math
import weakref

import numpy as _np

# numba 가속 (선택). 아래 각 njit 커널은 옆의 순수 파이썬 함수를 bit-identical
# (수치 동일)하게 옮긴 것. numba 없으면 순수 파이썬 경로로 폴백.
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
    """pos에 놓인 블록 i(orientation o)의 bounding box, bay 좌표계."""
    x, y = pos
    x0, y0, x1, y1 = pre.bbox[i][o]
    return (x0 + x, y0 + y, x1 + x, y1 + y)


def num_layers(pre, i: int, o: int) -> int:
    return len(pre.poly[i][o])


# -----------------------------------------------------------------------------
# 겹침 프리미티브
# -----------------------------------------------------------------------------

def bbox_overlap(a: tuple, b: tuple) -> bool:
    """엄격 AABB 겹침 (변/코너 공유는 겹침 아님), utils._bb_overlap과 동일.
    a, b = (min_x, min_y, max_x, max_y)."""
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _in_ring_py(x, y, ring):
    """꼭짓점 리스트에 대한 ray-cast point-in-ring (순수 파이썬 폴백)."""
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
    """(n,2) float64 ring에 대한 njit ray-cast -- _in_ring_py를 bit-identical
    (수치 동일)하게 옮긴 것."""
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
    """ring r의 exterior float64 배열을 지연 생성해 붙이고 반환."""
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
    """(px, py)가 ring 집합 내부에 엄격히 있으면 True (어떤 exterior 안이면서
    그 hole 어디에도 안 들어감). 변 위의 점은 바깥으로 침 (변 공유는 충돌 아님)."""
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
    """ring 집합을 원점 대칭 (NFP(a,b) -> NFP(b,a) = -NFP(a,b))."""
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


# reflect된 NFP ring의 run 단위 memo (`moving < fixed` 분기). reflect된 ring은
# 위치 무관이라 캐시하면 후보 위치마다 다시 reflect 안 해도 된다. memo가 돌려주는
# 것은 공유되는 read-only 객체이니 모든 호출자는 읽기 전용으로만 쓴다. NFPCache를
# 키로 WeakKeyDictionary에 담아 worker PRE로 pickle되지 않고 PRE와 함께 해제된다.
# `fixed < moving` 분기는 이미 NFPCache._cache를 직접 친다.
_REL_MEMO: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()
import os as _os                                             # noqa: E402
_REL_CAP = int(_os.environ.get("OGC_NFP_CAP", "250000"))


def _rel_memo(nfp):
    m = _REL_MEMO.get(nfp)
    if m is None:
        m = _REL_MEMO[nfp] = {}
    return m


def _memo_put(memo, key, rings):
    # 프로세스-수명 reflect-NFP 미러의 무한성장 상한(순수 메모, 결과 비트동일).
    if len(memo) >= _REL_CAP:
        memo.clear()
    memo[key] = rings


def relative_nfp(pre, moving: int, fixed: int, o_m: int, o_f: int, k: int) -> list:
    """layer k에서 `moving`의 `fixed`에 대한 NFP. footprint가 겹칠 필요충분조건이
    (pos_moving - pos_fixed)가 내부에 있는 것이 되도록 표현.

    캐시는 정렬된 쌍에 대해서만 NFP 저장:
      fixed  < moving : 캐시 키 (fixed, moving); 이미 올바른 offset.
      moving < fixed  : 캐시 키 (moving, fixed); reflect해서 offset 부호 뒤집음.
    """
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
    """`moving`의 layer k_m을 `fixed`의 layer k_f에 대해 본 crane NFP. 두 layer가
    겹칠 필요충분조건이 (pos_moving - pos_fixed)가 내부에 있는 것이 되도록 표현.
    relative_nfp와 같은 정렬-쌍 + reflection 처리, 단 layer 인덱스는 독립
    (crane sweep은 moving layer k를 resident layer j >= k와 짝지음)."""
    if fixed < moving:
        return pre.nfp.crane(fixed, moving, o_f, o_m, k_f, k_m)
    memo = _rel_memo(pre.nfp)
    key = ("c", moving, fixed, o_m, o_f, k_m, k_f)
    rings = memo.get(key)
    if rings is None:
        rings = reflect_rings(pre.nfp.crane(moving, fixed, o_m, o_f, k_m, k_f))
        _memo_put(memo, key, rings)
    return rings


# -----------------------------------------------------------------------------
# bbox 헬퍼 (destroy 연산자용)
# -----------------------------------------------------------------------------

def centroid_of_bbox(bb: tuple) -> tuple:
    return ((bb[0] + bb[2]) * 0.5, (bb[1] + bb[3]) * 0.5)


def bay_diagonal(prob_info, j: int) -> float:
    b = prob_info["bays"][j]
    return math.hypot(b["width"], b["height"])
