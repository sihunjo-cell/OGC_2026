"""저수준 기하 커널: 면적/bbox/단순화/볼록분할/Minkowski/NFP 생성 (hot loop은 shapely 배제)."""

from __future__ import annotations

import math
from typing import Optional

import numpy as _np
from shapely.geometry import Polygon as _ShapelyPolygon, MultiPoint as _MultiPoint
from shapely.ops import unary_union as _unary_union, triangulate as _triangulate

from .config import EPS

# numba 가속 (선택, 부재 시 py 폴백 = 비트동일)
try:
    from numba import njit as _njit
    _HAVE_NUMBA = True
except Exception:                        # pragma: no cover
    _HAVE_NUMBA = False
    def _njit(*a, **k):                   # no-op 데코레이터 (이 경우 커널 미호출)
        def _wrap(f):
            return f
        return _wrap


def polygon_area(pts: list) -> float:
    """shoelace 면적(절댓값)."""
    n = len(pts)
    if n < 3:
        return 0.0
    s = 0.0
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) * 0.5


def bounding_box(pts: list) -> tuple[float, float, float, float]:
    """(min_x, min_y, max_x, max_y)."""
    xs = [v[0] for v in pts]
    ys = [v[1] for v in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _perp_dist(p, a, b) -> float:
    """점-직선 수직 거리."""
    ax, ay = a
    bx, by = b
    px, py = p
    dx = bx - ax
    dy = by - ay
    if dx == 0.0 and dy == 0.0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    projx = ax + t * dx
    projy = ay + t * dy
    return math.hypot(px - projx, py - projy)


def _dp_open(pts: list, tol: float) -> list:
    """열린 폴리라인 Douglas-Peucker (끝점 유지)."""
    if len(pts) < 3:
        return list(pts)
    a, b = pts[0], pts[-1]
    dmax, idx = -1.0, -1
    for i in range(1, len(pts) - 1):
        d = _perp_dist(pts[i], a, b)
        if d > dmax:
            dmax, idx = d, i
    if dmax > tol:
        left = _dp_open(pts[:idx + 1], tol)
        right = _dp_open(pts[idx:], tol)
        return left[:-1] + right
    return [a, b]


def _douglas_peucker_closed(pts: list, tol: float) -> list:
    """닫힌 ring Douglas-Peucker (pts[0] = 기준점 보존)."""
    n = len(pts)
    if n <= 3:
        return list(pts)
    p0 = pts[0]
    far = max(range(n), key=lambda k: (pts[k][0] - p0[0]) ** 2 + (pts[k][1] - p0[1]) ** 2)
    if far == 0:
        return list(pts)
    first = _dp_open(pts[0:far + 1], tol)               # p0 ... p_far
    second = _dp_open(pts[far:] + [pts[0]], tol)         # p_far ... p_{n-1} ... p0
    return first[:-1] + second[:-1]


def simplify_layer(pts: list, tol: float) -> list:
    """layer ring 단순화 (tol<=0 = 원본 유지)."""
    ring = [(float(v[0]), float(v[1])) for v in pts]
    if tol <= 0.0 or len(ring) <= 3:
        return ring
    res = _douglas_peucker_closed(ring, tol)
    if len(res) < 3:
        return ring
    return res


def _shapely_polygon(pts: list) -> Optional[_ShapelyPolygon]:
    """유효 Polygon 생성 (buffer(0) 복구, degenerate = None)."""
    if pts is None or len(pts) < 3:
        return None
    try:
        p = _ShapelyPolygon(pts)
        if not p.is_valid:
            p = p.buffer(0)
        return p if (not p.is_empty and p.geom_type in ("Polygon", "MultiPolygon")) else None
    except Exception:
        return None


def _convex_partition(pts: list) -> list:
    """triangulation 기반 볼록 분할 (내부 삼각형만, 없으면 hull 폴백)."""
    poly = _shapely_polygon(pts)
    if poly is None:
        return []
    parts = []
    try:
        for tri in _triangulate(poly):
            if tri.is_empty or tri.area <= EPS:
                continue
            if poly.contains(tri.representative_point()):
                parts.append([(x, y) for x, y in list(tri.exterior.coords)[:-1]])
    except Exception:
        parts = []
    if not parts:
        hull = poly.convex_hull
        if hull.geom_type == "Polygon" and not hull.is_empty:
            parts = [[(x, y) for x, y in list(hull.exterior.coords)[:-1]]]
    return parts


def _minkowski_convex(P: list, Q: list) -> Optional[_ShapelyPolygon]:
    """볼록쌍 Minkowski 합 (정점 쌍합 hull)."""
    sums = [(px + qx, py + qy) for (px, py) in P for (qx, qy) in Q]
    if len(sums) < 3:
        return None
    hull = _MultiPoint(sums).convex_hull
    return hull if (hull.geom_type == "Polygon" and not hull.is_empty and hull.area > 0) else None


def geom_to_rings(geom) -> list:
    """shapely geom -> 정점 ring 리스트."""
    rings = []

    def _add(poly):
        ext = [(x, y) for x, y in list(poly.exterior.coords)[:-1]]
        holes = [[(x, y) for x, y in list(r.coords)[:-1]] for r in poly.interiors]
        rings.append({"ext": ext, "holes": holes})

    if geom is None or geom.is_empty:
        return rings
    gt = geom.geom_type
    if gt == "Polygon":
        _add(geom)
    elif gt in ("MultiPolygon", "GeometryCollection"):
        for g in geom.geoms:
            if g.geom_type == "Polygon" and not g.is_empty:
                _add(g)
    return rings


def nfp_rings(A_pts: list, B_pts: list) -> list:
    """NFP(A,B) = A⊕(−B) ring (shapely 레퍼런스 경로)."""
    if not A_pts or not B_pts:
        return []
    negB = [(-x, -y) for (x, y) in B_pts]
    A_parts = _convex_partition(A_pts)
    B_parts = _convex_partition(negB)
    if not A_parts or not B_parts:
        return []
    pieces = []
    for a in A_parts:
        for b in B_parts:
            m = _minkowski_convex(a, b)
            if m is not None:
                pieces.append(m)
    if not pieces:
        return []
    try:
        u = _unary_union(pieces)
    except Exception:
        return []
    return geom_to_rings(u)


def _is_convex(pts: list) -> bool:
    """볼록 판정 (회전 부호 동일)."""
    n = len(pts)
    if n < 4:
        return True                                   # 삼각형 이하는 볼록
    sign = 0
    for i in range(n):
        ax, ay = pts[i]
        bx, by = pts[(i + 1) % n]
        cx, cy = pts[(i + 2) % n]
        cross = (bx - ax) * (cy - by) - (by - ay) * (cx - bx)
        if cross > EPS:
            if sign < 0:
                return False
            sign = 1
        elif cross < -EPS:
            if sign > 0:
                return False
            sign = -1
    return True


def _monotone_chain(pts: list) -> list:
    """monotone-chain convex hull."""
    pts = sorted(set(pts))
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


@_njit(cache=True)
def _mink_kernel(P, Q):                   # pragma: no cover (njit)
    """njit Minkowski hull (py 경로와 비트동일 -- 계보는 invariants 원장)."""
    nP = P.shape[0]
    nQ = Q.shape[0]
    N = nP * nQ
    sx = _np.empty(N, _np.float64)
    sy = _np.empty(N, _np.float64)
    idx = 0
    for a in range(nP):
        for b in range(nQ):
            sx[idx] = P[a, 0] + Q[b, 0]
            sy[idx] = P[a, 1] + Q[b, 1]
            idx += 1
    # 사전식 삽입정렬 (py 튜플 순서 일치)
    for i in range(1, N):
        kx = sx[i]; ky = sy[i]; j = i - 1
        while j >= 0 and (sx[j] > kx or (sx[j] == kx and sy[j] > ky)):
            sx[j + 1] = sx[j]; sy[j + 1] = sy[j]; j -= 1
        sx[j + 1] = kx; sy[j + 1] = ky
    # 인접 dedup (sorted(set(...))과 동일)
    ux = _np.empty(N, _np.float64); uy = _np.empty(N, _np.float64); m = 0
    for i in range(N):
        if m == 0 or sx[i] != ux[m - 1] or sy[i] != uy[m - 1]:
            ux[m] = sx[i]; uy[m] = sy[i]; m += 1
    if m <= 2:
        out = _np.empty((m, 2), _np.float64)
        for i in range(m):
            out[i, 0] = ux[i]; out[i, 1] = uy[i]
        return out
    # monotone chain
    lx = _np.empty(m, _np.float64); ly = _np.empty(m, _np.float64); ln = 0
    for i in range(m):
        while ln >= 2 and ((lx[ln - 1] - lx[ln - 2]) * (uy[i] - ly[ln - 2])
                           - (ly[ln - 1] - ly[ln - 2]) * (ux[i] - lx[ln - 2])) <= 0:
            ln -= 1
        lx[ln] = ux[i]; ly[ln] = uy[i]; ln += 1
    vx = _np.empty(m, _np.float64); vy = _np.empty(m, _np.float64); un = 0
    for i in range(m - 1, -1, -1):
        while un >= 2 and ((vx[un - 1] - vx[un - 2]) * (uy[i] - vy[un - 2])
                           - (vy[un - 1] - vy[un - 2]) * (ux[i] - vx[un - 2])) <= 0:
            un -= 1
        vx[un] = ux[i]; vy[un] = uy[i]; un += 1
    R = (ln - 1) + (un - 1)
    out = _np.empty((R, 2), _np.float64)
    k = 0
    for i in range(ln - 1):
        out[k, 0] = lx[i]; out[k, 1] = ly[i]; k += 1
    for i in range(un - 1):
        out[k, 0] = vx[i]; out[k, 1] = vy[i]; k += 1
    return out


def _minkowski_convex_pure(P: list, Q: list) -> list:
    """볼록쌍 Minkowski 합 (njit 우선, py 폴백 = 비트동일)."""
    if _HAVE_NUMBA and P and Q:
        try:
            arr = _mink_kernel(_np.asarray(P, _np.float64), _np.asarray(Q, _np.float64))
            return [(float(arr[i, 0]), float(arr[i, 1])) for i in range(arr.shape[0])]
        except Exception:                # pragma: no cover
            pass
    sums = [(px + qx, py + qy) for (px, py) in P for (qx, qy) in Q]
    return _monotone_chain(sums)


def _edge_key(p) -> tuple:
    return (round(p[0], 6), round(p[1], 6))


def _shared_edge(A: list, B: list):
    """공유 경계 edge 탐색 -> (ia, jv) 또는 None."""
    nB = len(B)
    idxB = {_edge_key(p): j for j, p in enumerate(B)}
    nA = len(A)
    for ia in range(nA):
        ku = _edge_key(A[ia])
        kv = _edge_key(A[(ia + 1) % nA])
        jv = idxB.get(kv)
        if jv is not None and idxB.get(ku) is not None and _edge_key(B[(jv + 1) % nB]) == ku:
            return ia, jv
    return None


def _merge_along_edge(A: list, B: list, ia: int, jv: int) -> list:
    """공유 edge 기준 두 폴리곤 병합."""
    nA, nB = len(A), len(B)
    merged = []
    i = (ia + 1) % nA
    while True:                          # A를 v에서 u까지 한 바퀴 (v부터 A 전체)
        merged.append(A[i])
        if i == ia:
            break
        i = (i + 1) % nA
    i = (jv + 2) % nB                     # B를 u 다음부터 v 전까지
    while i != jv:
        merged.append(B[i])
        i = (i + 1) % nB
    return merged


def _hertel_mehlhorn(pieces: list) -> list:
    """Hertel-Mehlhorn 병합 (볼록 AND 면적 보존만 채택)."""
    pieces = [list(p) for p in pieces]
    changed = True
    while changed:
        changed = False
        na = len(pieces)
        for a in range(na):
            for b in range(a + 1, na):
                se = _shared_edge(pieces[a], pieces[b])
                if se is None:
                    continue
                merged = _merge_along_edge(pieces[a], pieces[b], se[0], se[1])
                if len(merged) >= 3 and _is_convex(merged):
                    am = polygon_area(merged)
                    if abs(am - polygon_area(pieces[a]) - polygon_area(pieces[b])) <= 1e-6 * max(1.0, am):
                        pieces = [pieces[k] for k in range(na) if k not in (a, b)]
                        pieces.append(merged)
                        changed = True
                        break
            if changed:
                break
    return pieces


def convex_decompose(pts: list) -> list:
    """볼록 분할 (볼록 = 그대로, 비볼록 = triangulate + Hertel-Mehlhorn)."""
    if pts is None or len(pts) < 3:
        return []
    if _is_convex(pts):
        return [list(pts)]
    tris = _convex_partition(pts)
    if len(tris) <= 1:
        return tris
    return _hertel_mehlhorn(tris)


def nfp_rings_hybrid(A_parts: list, B_parts: list) -> list:
    """FAST 경로 NFP (nfp_rings와 비트동일, shapely Minkowski 생략)."""
    if not A_parts or not B_parts:
        return []

    # 볼록x볼록 = union 불필요
    if len(A_parts) == 1 and len(B_parts) == 1:
        b_neg = [(-x, -y) for (x, y) in B_parts[0]]
        hull = _minkowski_convex_pure(A_parts[0], b_neg)
        return [{"ext": hull, "holes": []}] if len(hull) >= 3 else []

    # 비볼록: 조각 쌍합 후 union
    polys = []
    for a in A_parts:
        for b in B_parts:
            b_neg = [(-x, -y) for (x, y) in b]
            hull = _minkowski_convex_pure(a, b_neg)
            if len(hull) < 3:
                continue
            p = _ShapelyPolygon(hull)
            if not p.is_valid:
                p = p.buffer(0)
            if not p.is_empty and p.area > 0:
                polys.append(p)
    if not polys:
        return []
    try:
        u = _unary_union(polys)
    except Exception:
        return []
    return geom_to_rings(u)
