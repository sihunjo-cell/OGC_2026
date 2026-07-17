# Phase2/nestle.py
"""mask-비가시 합법 앵커의 exact 공간 판정 (판정-동치 numba fast 경로, 계약 = invariants 원장)."""

from __future__ import annotations

import numpy as np
from shapely.geometry import Polygon
from shapely.ops import unary_union
from shapely.prepared import prep

EPS_AREA = 1e-9
_EPS_LO = 5e-10    # <= 확정 허용
_EPS_HI = 2e-9     # >= 확정 차단; (LO, HI) razor band = shapely 재판정

try:
    from numba import njit as _njit
    _HAVE_NUMBA = True
except Exception:                                    # pragma: no cover
    _HAVE_NUMBA = False

    def _njit(*a, **k):                              # no-op 데코레이터
        def _wrap(f):
            return f
        return _wrap


@_njit(cache=True, fastmath=False)
def _clip_area(sub, ns, clip, nc):
    """볼록쌍 클리핑 교차 면적 (경계 포함 -> 변접촉 = 면적 0)."""
    buf_in = np.empty((ns + nc + 8, 2), np.float64)
    buf_out = np.empty((ns + nc + 8, 2), np.float64)
    m = ns
    for t in range(ns):
        buf_in[t, 0] = sub[t, 0]
        buf_in[t, 1] = sub[t, 1]
    for e in range(nc):
        ax, ay = clip[e, 0], clip[e, 1]
        e1 = e + 1
        if e1 == nc:
            e1 = 0
        bx, by = clip[e1, 0], clip[e1, 1]
        ex, ey = bx - ax, by - ay
        cnt = 0
        for t in range(m):
            px, py = buf_in[t, 0], buf_in[t, 1]
            t1 = t + 1
            if t1 == m:
                t1 = 0
            qx, qy = buf_in[t1, 0], buf_in[t1, 1]
            dp = ex * (py - ay) - ey * (px - ax)     # >0: clip 변의 왼쪽(내부)
            dq = ex * (qy - ay) - ey * (qx - ax)
            if dp >= 0.0:
                buf_out[cnt, 0] = px
                buf_out[cnt, 1] = py
                cnt += 1
            if (dp > 0.0 and dq < 0.0) or (dp < 0.0 and dq > 0.0):
                s = dp / (dp - dq)
                buf_out[cnt, 0] = px + s * (qx - px)
                buf_out[cnt, 1] = py + s * (qy - py)
                cnt += 1
        m = cnt
        if m == 0:
            return 0.0
        for t in range(m):
            buf_in[t, 0] = buf_out[t, 0]
            buf_in[t, 1] = buf_out[t, 1]
    a = 0.0
    for t in range(m):
        t1 = t + 1
        if t1 == m:
            t1 = 0
        a += buf_in[t, 0] * buf_in[t1, 1] - buf_in[t1, 0] * buf_in[t, 1]
    return abs(a) * 0.5


@_njit(cache=True, fastmath=False)
def _stack_overlap_area(cv, cn, cb, dx, dy, rv, rn, rb, hi):
    """후보x상주 볼록조각 쌍면적 합 (bbox 프리필터, hi 초과 조기 반환)."""
    acc = 0.0
    for a in range(cn.shape[0]):
        ax0 = cb[a, 0] + dx
        ay0 = cb[a, 1] + dy
        ax1 = cb[a, 2] + dx
        ay1 = cb[a, 3] + dy
        na = cn[a]
        for b in range(rn.shape[0]):
            if (ax1 <= rb[b, 0] or rb[b, 2] <= ax0
                    or ay1 <= rb[b, 1] or rb[b, 3] <= ay0):
                continue
            sub = np.empty((na, 2), np.float64)
            for t in range(na):
                sub[t, 0] = cv[a, t, 0] + dx
                sub[t, 1] = cv[a, t, 1] + dy
            acc += _clip_area(sub, na, rv[b], rn[b])
            if acc > hi:
                return acc
    return acc


def world_poly(pre, i: int, o: int, k: int, pos):
    ring = pre.poly[i][o][k]
    return Polygon([(x + pos[0], y + pos[1]) for (x, y) in ring])


def _pack_pieces(piece_arrays):
    """볼록조각 리스트 -> (verts, counts, bbox) 패딩 팩."""
    P = len(piece_arrays)
    V = 0
    for p in piece_arrays:
        if p.shape[0] > V:
            V = p.shape[0]
    verts = np.zeros((P, V, 2), np.float64)
    cnts = np.zeros(P, np.int64)
    bbox = np.zeros((P, 4), np.float64)
    for a, p in enumerate(piece_arrays):
        n = p.shape[0]
        verts[a, :n] = p
        cnts[a] = n
        bbox[a, 0] = p[:, 0].min()
        bbox[a, 1] = p[:, 1].min()
        bbox[a, 2] = p[:, 0].max()
        bbox[a, 3] = p[:, 1].max()
    return verts, cnts, bbox


class ExactSpace:
    """bay별 상주 exact 기하 lazy 캐시 (token = raster.ver 동기)."""

    def __init__(self, pre, fast: bool = True):
        self.pre = pre
        self.fast = bool(fast) and _HAVE_NUMBA
        self._tok: dict = {}     # j -> token
        self._geoms: dict = {}   # (j, k) -> (prepared, union) | (None, None)
        self._fastg: dict = {}   # (j, k) -> (rv, rn, rb) | () 상주없음 | None 조각부재
        self._cnd: dict = {}     # (i, o, k) -> (cv, cn, cb) | None 조각부재

    def _pieces(self, i, o, k):
        key = (i, o, k)
        if key in self._cnd:
            return self._cnd[key]
        parts = self.pre.nfp._decompose(i, o, k)
        if not parts:
            self._cnd[key] = None
            return None
        arrs = []
        for p in parts:
            arr = np.asarray(p, np.float64)
            if arr.shape[0] < 3:
                continue
            x, y = arr[:, 0], arr[:, 1]
            a2 = float(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y))
            if a2 < 0.0:                       # CW -> CCW
                arr = arr[::-1].copy()
            arrs.append(arr)
        g = _pack_pieces(arrs) if arrs else None
        self._cnd[key] = g
        return g

    def _sync(self, j, token):
        if self._tok.get(j) != token:
            for key in [key for key in self._geoms if key[0] == j]:
                del self._geoms[key]
            for key in [key for key in self._fastg if key[0] == j]:
                del self._fastg[key]
            self._tok[j] = token

    def _geoms_ge(self, j, token, k, residents, coords, orient):
        self._sync(j, token)
        g = self._geoms.get((j, k))
        if g is None:
            polys = []
            for i2 in residents:
                lays = self.pre.poly[i2][orient[i2]]
                for k2 in range(k, len(lays)):
                    polys.append(world_poly(self.pre, i2, orient[i2], k2,
                                            coords[i2]))
            if polys:
                u = unary_union(polys)
                g = (prep(u), u)
            else:
                g = (None, None)
            self._geoms[(j, k)] = g
        return g

    def _fast_ge(self, j, token, k, residents, coords, orient):
        """상주 layer>=k 조각 팩 (()=상주 없음, None=분해 불가 -> shapely 폴백)."""
        self._sync(j, token)
        if (j, k) in self._fastg:
            return self._fastg[(j, k)]
        arrs = []
        ok = True
        for i2 in residents:
            o2 = orient[i2]
            cx, cy = coords[i2]
            lays = self.pre.poly[i2][o2]
            for k2 in range(k, len(lays)):
                pk = self._pieces(i2, o2, k2)
                if pk is None:
                    ok = False
                    break
                cv, cn, cb = pk
                for a in range(cn.shape[0]):
                    n = int(cn[a])
                    arr = cv[a, :n].copy()
                    arr[:, 0] += cx
                    arr[:, 1] += cy
                    arrs.append(arr)
            if not ok:
                break
        if not ok:
            g = None
        elif not arrs:
            g = ()
        else:
            g = _pack_pieces(arrs)
        self._fastg[(j, k)] = g
        return g

    def space_ok(self, j, token, q, o, pos, residents, coords, orient) -> bool:
        """후보의 exact 공간충돌 없음 판정 (layer k vs 상주 >=k, 변접촉 합법)."""
        lays_q = self.pre.poly[q][o]
        for k in range(len(lays_q)):
            if self.fast:
                fr = self._fast_ge(j, token, k, residents, coords, orient)
                if fr == ():                       # 상주 조각 없음
                    continue
                if fr is not None:
                    cp = self._pieces(q, o, k)
                    if cp is not None:
                        area = _stack_overlap_area(
                            cp[0], cp[1], cp[2], float(pos[0]), float(pos[1]),
                            fr[0], fr[1], fr[2], _EPS_HI)
                        if area <= _EPS_LO:
                            continue
                        if area >= _EPS_HI:
                            return False
                        # razor band -> shapely 재판정
            pg, ug = self._geoms_ge(j, token, k, residents, coords, orient)
            if pg is None:
                continue
            cand = world_poly(self.pre, q, o, k, pos)
            if pg.intersects(cand):
                if ug.intersection(cand).area > EPS_AREA:
                    return False
        return True
