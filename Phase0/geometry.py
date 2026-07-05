"""Phase0.geometry -- 0.2용 저수준 지오메트리 커널: 면적, bbox, Douglas-Peucker,
볼록 분할, 볼록 Minkowski 합, No-Fit-Polygon(NFP) 생성.

shapely는 지오메트리 생성(1회)에만 사용. NFP는 순수 정점 ring으로 반환해서
Phase 2 hot loop은 shapely를 안 거침.
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as _np
from shapely.geometry import Polygon as _ShapelyPolygon, MultiPoint as _MultiPoint
from shapely.ops import unary_union as _unary_union, triangulate as _triangulate

from .config import EPS

# Minkowski hull 커널용 numba 가속(선택). 없으면 순수 파이썬 monotone chain으로
# 폴백하며 결과는 bit-identical.
try:
    from numba import njit as _njit
    _HAVE_NUMBA = True
except Exception:                        # pragma: no cover
    _HAVE_NUMBA = False
    def _njit(*a, **k):                   # 데코레이터 no-op. 이 경우 커널은 호출 안 됨
        def _wrap(f):
            return f
        return _wrap


# -----------------------------------------------------------------------------
# 스칼라 지오메트리 (순수 파이썬 -- hot loop에 간접적으로 들어감)
# -----------------------------------------------------------------------------

def polygon_area(pts: list) -> float:
    """단순 폴리곤의 shoelace 면적(절댓값)."""
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
    """정점 리스트의 (min_x, min_y, max_x, max_y)."""
    xs = [v[0] for v in pts]
    ys = [v[1] for v in pts]
    return (min(xs), min(ys), max(xs), max(ys))


# -----------------------------------------------------------------------------
# Douglas-Peucker 폴리곤 단순화
# -----------------------------------------------------------------------------

def _perp_dist(p, a, b) -> float:
    """점 p에서 a, b를 지나는 무한 직선까지의 수직 거리."""
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
    """열린 폴리라인에 대한 Douglas-Peucker. 양 끝점은 항상 유지."""
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
    """닫힌 ring용 Douglas-Peucker (정점 중복 없음).

    pts[0]과 가장 먼 정점에서 둘로 나눠 각각 열린 폴리라인으로 단순화.
    pts[0]은 항상 보존해서 layer 0의 기준점 (0, 0)을 유지.
    """
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
    """한 layer의 정점 ring을 (x, y) 튜플 리스트로 단순화.

    tol <= 0이면 원본 정점 유지(안전 기본값). 삼각형 밑으로는 절대 안 줄이고,
    첫 정점(layer 0의 기준점)은 항상 보존.
    """
    ring = [(float(v[0]), float(v[1])) for v in pts]
    if tol <= 0.0 or len(ring) <= 3:
        return ring
    res = _douglas_peucker_closed(ring, tol)
    if len(res) < 3:
        return ring
    return res


# -----------------------------------------------------------------------------
# 볼록 분할 + Minkowski 합 + NFP
# -----------------------------------------------------------------------------

def _shapely_polygon(pts: list) -> Optional[_ShapelyPolygon]:
    """정점 ring으로 유효한 shapely Polygon 생성(buffer(0)로 복구). degenerate면 None."""
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
    """단순 폴리곤을 shapely triangulation으로 볼록 조각들로 분할.

    대표점이 폴리곤 내부에 있는 삼각형만 유지(오목부를 가로지르는 건 버림).
    볼록 정점 ring 반환. triangulation이 쓸 게 없으면 convex hull로 폴백.
    """
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
    """두 볼록 폴리곤의 Minkowski 합 = 정점 쌍합의 convex hull."""
    sums = [(px + qx, py + qy) for (px, py) in P for (qx, qy) in Q]
    if len(sums) < 3:
        return None
    hull = _MultiPoint(sums).convex_hull
    return hull if (hull.geom_type == "Polygon" and not hull.is_empty and hull.area > 0) else None


def geom_to_rings(geom) -> list:
    """shapely Polygon/MultiPolygon/GeometryCollection을 순수 정점 ring으로 변환:
    [{"ext": [(x,y),...], "holes": [[(x,y),...], ...]}, ...] (shapely 비의존)."""
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
    """NFP(A, B) = A (+) (-B)를 순수 정점 ring으로 (SHAPELY 레퍼런스 경로).

    양쪽을 볼록 분할한 뒤 조각끼리 쌍으로 합하고 union:
    (U A_i) (+) (U -B_j) = U_{i,j} (A_i (+) -B_j).
    """
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


# -----------------------------------------------------------------------------
# FAST 경로: union 없는 볼록 조각, 순수 파이썬 (hot loop에 shapely 없음)
# -----------------------------------------------------------------------------

def _is_convex(pts: list) -> bool:
    """폴리곤 ring이 볼록이면 True (모든 회전 부호가 같음)."""
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
    """Andrew's monotone-chain convex hull (CCW, 마지막 점 중복 없음)."""
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
    """볼록 Minkowski hull을 njit 하나로 처리 (쌍합 -> 정렬 -> dedup -> monotone
    chain). _monotone_chain의 float 연산을 그대로 따라해서 hull이 순수 파이썬
    경로와 bit-identical."""
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
    # 사전식 삽입정렬 (x, 그다음 y) -- 파이썬 튜플 순서와 일치
    for i in range(1, N):
        kx = sx[i]; ky = sy[i]; j = i - 1
        while j >= 0 and (sx[j] > kx or (sx[j] == kx and sy[j] > ky)):
            sx[j + 1] = sx[j]; sy[j + 1] = sy[j]; j -= 1
        sx[j + 1] = kx; sy[j + 1] = ky
    # 인접 정확 dedup -> sorted(set(...))과 동일
    ux = _np.empty(N, _np.float64); uy = _np.empty(N, _np.float64); m = 0
    for i in range(N):
        if m == 0 or sx[i] != ux[m - 1] or sy[i] != uy[m - 1]:
            ux[m] = sx[i]; uy[m] = sy[i]; m += 1
    if m <= 2:
        out = _np.empty((m, 2), _np.float64)
        for i in range(m):
            out[i, 0] = ux[i]; out[i, 1] = uy[i]
        return out
    # monotone chain (cross <= 0이면 pop), lower 다음 upper
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
    """두 볼록 폴리곤의 Minkowski 합 = 정점 쌍합의 convex hull.
    numba 있으면 njit 커널 사용(bit-identical), 없으면 순수 파이썬 monotone
    chain. 커널 실패 시 순수 파이썬으로 폴백."""
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
    """폴리곤 A의 유향 edge u->v를 B가 v->u로 지나가면(공유 경계 edge)
    (ia, jv) 반환: A[ia]=u, A[ia+1]=v, B[jv]=v."""
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
    """공유 edge를 사이에 둔 인접 볼록 폴리곤 둘을 하나의 ring으로 병합."""
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
    """triangulation을 더 적은 볼록 조각으로 병합(Hertel-Mehlhorn): 두 이웃의
    union이 볼록으로 유지되면 공유 대각선을 없앰. 면적 보존이라 NFP union은
    그대로."""
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
                # 볼록 AND 면적 보존일 때만 채택. 면적 검사가 잘못된/자기교차
                # ring을 만든 병합을 걸러냄.
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
    """단순 폴리곤을 볼록 정점 ring 리스트로 볼록 분할.

    볼록 입력은 조각 하나로 반환. 비볼록 입력은 triangulation(shapely) 후
    Hertel-Mehlhorn으로 최소 개수의 볼록 조각으로 병합.
    """
    if pts is None or len(pts) < 3:
        return []
    if _is_convex(pts):
        return [list(pts)]
    tris = _convex_partition(pts)
    if len(tris) <= 1:
        return tris
    return _hertel_mehlhorn(tris)


def nfp_rings_hybrid(A_parts: list, B_parts: list) -> list:
    """순수 파이썬 Minkowski + shapely union으로 NFP ring 생성 (HYBRID 경로).

    nfp_rings()와 bit-identical한 ring을 만들되 비싼 shapely MultiPoint
    Minkowski는 건너뜀. union은 여전히 shapely라 ring 정점은 정확.
    """
    if not A_parts or not B_parts:
        return []

    # 볼록 x 볼록 (각각 조각 1개): NFP가 볼록 폴리곤 하나라 union 불필요 --
    # Minkowski hull을 바로 반환 (shapely 없이 정확).
    if len(A_parts) == 1 and len(B_parts) == 1:
        b_neg = [(-x, -y) for (x, y) in B_parts[0]]
        hull = _minkowski_convex_pure(A_parts[0], b_neg)
        return [{"ext": hull, "holes": []}] if len(hull) >= 3 else []

    # 여러 조각 (비볼록 layer): 순수 파이썬 Minkowski 조각들 만든 뒤 shapely union.
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


def nfp_pieces(A_parts: list, B_parts: list) -> list:
    """NFP를 union 없는 볼록 조각으로 (FAST 경로, shapely 없음).

    각 조각은 구멍 없는 볼록 ring. 점이 NFP 안에 있다 <=> 어떤 조각 안에 있다
    (p in U C_i <=> exists i: p in C_i).
    """
    if not A_parts or not B_parts:
        return []
    rings = []
    for a in A_parts:
        for b in B_parts:
            b_neg = [(-x, -y) for (x, y) in b]
            hull = _minkowski_convex_pure(a, b_neg)
            if len(hull) >= 3:
                rings.append({"ext": hull, "holes": []})
    return rings
