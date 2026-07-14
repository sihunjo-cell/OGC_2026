"""Phase2.geometry_query -- 탐색 hot loop용 순수 파이썬 geometry 쿼리.

Phase 0이 미리 계산해 둔 순수 꼭짓점 ring/테이블(PRE.nfp, PRE.poly, PRE.bbox)을
shapely 없이 사용. 좌표 변환, AABB/point-in-ring 겹침, NFP reflection +
relative-NFP 캐시 조회, contact 길이, 점수 헬퍼를 다룬다."""

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

def translate_layer(layer: list, dx: float, dy: float) -> list:
    return [(x + dx, y + dy) for (x, y) in layer]


def world_layers(pre, i: int, o: int, pos: tuple) -> list:
    """블록 i(orientation o)의 모든 layer를 기준점이 pos = (x, y)에 오도록 평행이동."""
    x, y = pos
    return [translate_layer(layer, x, y) for layer in pre.poly[i][o]]


def world_bbox(pre, i: int, o: int, pos: tuple) -> tuple:
    """pos에 놓인 블록 i(orientation o)의 bounding box, bay 좌표계."""
    x, y = pos
    x0, y0, x1, y1 = pre.bbox[i][o]
    return (x0 + x, y0 + y, x1 + x, y1 + y)


def num_orientations(pre, i: int) -> int:
    return len(pre.poly[i])


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


def _rel_memo(nfp):
    m = _REL_MEMO.get(nfp)
    if m is None:
        m = _REL_MEMO[nfp] = {}
    return m


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
        memo[key] = rings
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
        memo[key] = rings
    return rings


# -----------------------------------------------------------------------------
# Contact / 점수 헬퍼
# -----------------------------------------------------------------------------

def _seg_overlap_len(a1, a2, b1, b2) -> float:
    """두 선분이 공선(collinear)이면 겹치는 길이, 아니면 0."""
    dax, day = a2[0] - a1[0], a2[1] - a1[1]
    L = math.hypot(dax, day)
    if L < _EPS:
        return 0.0
    # b1, b2가 a1, a2를 지나는 무한직선 위에 있어야 함 (cross product ~ 0)
    if abs((b1[0] - a1[0]) * day - (b1[1] - a1[1]) * dax) > _EPS * max(1.0, L):
        return 0.0
    if abs((b2[0] - a1[0]) * day - (b2[1] - a1[1]) * dax) > _EPS * max(1.0, L):
        return 0.0
    ux, uy = dax / L, day / L
    tb1 = (b1[0] - a1[0]) * ux + (b1[1] - a1[1]) * uy
    tb2 = (b2[0] - a1[0]) * ux + (b2[1] - a1[1]) * uy
    lo = max(0.0, min(tb1, tb2))
    hi = min(L, max(tb1, tb2))
    return max(0.0, hi - lo)


@_njit(cache=True)
def _shared_edge_kernel(A, B, eps):       # pragma: no cover (njit)
    """njit -- shared_edge_length의 _seg_overlap_len 이중 루프를 bit-identical
    (수치 동일)하게 옮긴 것."""
    nA = A.shape[0]
    nB = B.shape[0]
    tot = 0.0
    for i in range(nA):
        a1x = A[i, 0]; a1y = A[i, 1]
        i2 = i + 1
        if i2 == nA:
            i2 = 0
        dax = A[i2, 0] - a1x
        day = A[i2, 1] - a1y
        L = math.hypot(dax, day)
        if L < eps:
            continue
        Lmax = eps * (L if L > 1.0 else 1.0)      # eps * max(1.0, L)
        ux = dax / L
        uy = day / L
        for j in range(nB):
            b1x = B[j, 0]; b1y = B[j, 1]
            j2 = j + 1
            if j2 == nB:
                j2 = 0
            b2x = B[j2, 0]; b2y = B[j2, 1]
            if abs((b1x - a1x) * day - (b1y - a1y) * dax) > Lmax:
                continue
            if abs((b2x - a1x) * day - (b2y - a1y) * dax) > Lmax:
                continue
            tb1 = (b1x - a1x) * ux + (b1y - a1y) * uy
            tb2 = (b2x - a1x) * ux + (b2y - a1y) * uy
            lo = tb1 if tb1 < tb2 else tb2         # min(tb1, tb2)
            if lo < 0.0:
                lo = 0.0                           # max(0.0, .)
            hi = tb1 if tb1 > tb2 else tb2         # max(tb1, tb2)
            if hi > L:
                hi = L                             # min(L, .)
            d = hi - lo
            if d > 0.0:                            # max(0.0, hi - lo)
                tot += d
    return tot


def shared_edge_length(A: list, B: list) -> float:
    """두 폴리곤 ring A, B가 일치(공선하며 겹침)하는 경계의 총 길이 -- 맞닿은
    폴리곤의 정확한 contact 길이."""
    if len(A) < 2 or len(B) < 2:
        return 0.0
    if _HAVE_NUMBA:
        try:
            return _shared_edge_kernel(_np.asarray(A, _np.float64),
                                       _np.asarray(B, _np.float64), _EPS)
        except Exception:                # pragma: no cover
            pass
    nA, nB = len(A), len(B)
    tot = 0.0
    for i in range(nA):
        a1, a2 = A[i], A[(i + 1) % nA]
        for j in range(nB):
            b1, b2 = B[j], B[(j + 1) % nB]
            tot += _seg_overlap_len(a1, a2, b1, b2)
    return tot


def bbox_overlap_area(a: tuple, b: tuple) -> float:
    """두 AABB의 겹침 면적 (분리면 0). 값싼 premarshalling(vertical-sweep) 근사로 사용."""
    ox = min(a[2], b[2]) - max(a[0], b[0])
    oy = min(a[3], b[3]) - max(a[1], b[1])
    if ox <= 0 or oy <= 0:
        return 0.0
    return ox * oy


def centroid_of_bbox(bb: tuple) -> tuple:
    return ((bb[0] + bb[2]) * 0.5, (bb[1] + bb[3]) * 0.5)


def bay_diagonal(prob_info, j: int) -> float:
    b = prob_info["bays"][j]
    return math.hypot(b["width"], b["height"])


# -----------------------------------------------------------------------------
# Convex hull + 선분 교차 (2.1 NIRI, 2.2 vertex+intersection)
# -----------------------------------------------------------------------------

def _shoelace(pts: list) -> float:
    n = len(pts)
    if n < 3:
        return 0.0
    s = 0.0
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) * 0.5


def convex_hull(points: list) -> list:
    """Andrew의 monotone-chain convex hull (반시계, 마지막 점 중복 없음).
    순수 파이썬, scipy 없음."""
    pts = sorted(set((float(x), float(y)) for x, y in points))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def convex_hull_area(points: list) -> float:
    """점 집합의 convex hull 면적."""
    return _shoelace(convex_hull(points))


def polygon_area(pts: list) -> float:
    """폴리곤 ring의 shoelace 면적."""
    return _shoelace(pts)


def seg_intersect(p1, p2, p3, p4):
    """선분 p1p2와 p3p4의 단일 교점, 없으면 None (평행 / 공선 / 안 만남).
    끝점이 닿는 것도 교차로 침."""
    x1, y1 = p1
    x2, y2 = p2
    x3, y3 = p3
    x4, y4 = p4
    d = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(d) < _EPS:
        return None
    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / d
    u = ((x1 - x3) * (y1 - y2) - (y1 - y3) * (x1 - x2)) / d
    if -1e-9 <= t <= 1 + 1e-9 and -1e-9 <= u <= 1 + 1e-9:
        return (x1 + t * (x2 - x1), y1 + t * (y2 - y1))
    return None
