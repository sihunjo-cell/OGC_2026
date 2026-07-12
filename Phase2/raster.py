# Phase2/raster.py
"""Phase2.raster -- v12-계열 라스터 기하 엔진 (ALGORITHM_PLAYBOOK §3.3 이식).

soundness 계약(플레이북 §3.1): 마스크는 각 layer 볼록껍질(convex hull)의
superset(닫힌 단위 정사각형을 touch하면 1). 정확 게이트의 NFP는 convex_decompose
조각들의 Minkowski 합으로 만들어져 오목 블록을 볼록껍질 수준까지 보수적으로 본다
(geom_mode="fast"에서 조각 합=hull 면적인 블록이 실재: 오목 노치를 채워서 폴리곤은
disjoint여도 hull이 겹치면 NFP가 충돌로 친다). 마스크를 폴리곤이 아니라 hull의
superset으로 두어야 [마스크 disjoint ⇒ 두 hull이 면적>0로 안 겹침 ⇒ (dx,dy)가
NFP 내부 아님, 변 접촉은 충돌 아님 ⇒ 정확 게이트 통과]가 증명된다. 그래서 scan이
feasible이라 한 앵커는 '현재 상주 대비 공간충돌 없음 + 크레인 진입(j>=k) 가능'이
참이다. 반대 방향(마스크는 겹치지만 실제론 합법)은 후보 하나 손해로만 나타난다.
시간축(내 EXIT 차단·역방향 차단)은 라스터가 증명하지 못하므로 호출자가 정확
게이트(crane_obstructed / crane_blocks_resident)를 통과시켜야 한다.

shapely는 마스크 빌드에서 층당 1회만 호출한다(hot loop 진입 금지)."""

from __future__ import annotations

import math

import numpy as np
import shapely
from shapely.geometry import Polygon


class Raster:
    def __init__(self, prob_info: dict, pre):
        self.pre = pre
        self.n_bays = pre.n_bays
        self.W = [int(math.ceil(b["width"])) for b in prob_info["bays"]]
        self.H = [int(math.ceil(b["height"])) for b in prob_info["bays"]]
        self.occ = [dict() for _ in range(self.n_bays)]   # occ[j][k] -> int16 (H, W) 카운트
        self.ver = [0] * self.n_bays
        self._mask = {}    # (i, o) -> (uint8 (K, MH, MW), mx0, my0)
        self._uge = {}     # j -> (ver, [int32 (H, W)] 층별 suffix union)
        self._uge_dil = {}  # j -> (ver, [int32 (H, W)] 8-이웃 팽창 suffix union)
        self._scan = {}    # j -> {(i, o): (ver, feas, mx0, my0)}
        self._field = {}   # j -> (ver, int32 (H+2, W+2) 접촉장, 테두리=벽)

    # -- 마스크 ---------------------------------------------------------------

    def mask(self, i: int, o: int):
        key = (i, o)
        m = self._mask.get(key)
        if m is None:
            m = self._mask[key] = self._build_mask(i, o)
        return m

    def _build_mask(self, i: int, o: int):
        bx0, by0, bx1, by1 = self.pre.bbox[i][o]
        mx0 = int(math.floor(bx0))
        my0 = int(math.floor(by0))
        MW = int(math.ceil(bx1)) - mx0
        MH = int(math.ceil(by1)) - my0
        layers = self.pre.poly[i][o]
        mask = np.zeros((len(layers), MH, MW), dtype=np.uint8)
        cs, rs = np.meshgrid(np.arange(MW, dtype=float), np.arange(MH, dtype=float))
        boxes = shapely.box(mx0 + cs.ravel(), my0 + rs.ravel(),
                            mx0 + cs.ravel() + 1.0, my0 + rs.ravel() + 1.0)
        for k, ring in enumerate(layers):
            # 볼록껍질을 rasterize: 정확 게이트 NFP가 convex_decompose(껍질 이하)
            # 기반이라 마스크는 hull의 superset이어야 [disjoint ⇒ NFP-clear]가
            # 성립한다(오목 폴리곤만 쓰면 노치에서 위반). hull은 bbox가 같아
            # mx0/my0/MH/MW 불변, IFP/stamp 정렬 그대로.
            hull = Polygon(ring).convex_hull
            hit = shapely.intersects(hull, boxes)   # 경계 touch 포함 => superset
            mask[k] = hit.reshape(MH, MW).astype(np.uint8)
        return mask, mx0, my0

    # -- 점유 (카운트라서 add/remove가 정확한 역연산) ---------------------------

    def add(self, j: int, i: int, o: int, pos):
        self._stamp(j, i, o, pos, 1)

    def remove(self, j: int, i: int, o: int, pos):
        self._stamp(j, i, o, pos, -1)

    def _stamp(self, j, i, o, pos, sgn):
        mask, mx0, my0 = self.mask(i, o)
        K, MH, MW = mask.shape
        r0 = int(pos[1]) + my0
        c0 = int(pos[0]) + mx0
        assert 0 <= r0 and 0 <= c0 and r0 + MH <= self.H[j] and c0 + MW <= self.W[j], \
            ("raster stamp out of bay", j, i, o, pos)
        occ = self.occ[j]
        for k in range(K):
            g = occ.get(k)
            if g is None:
                g = occ[k] = np.zeros((self.H[j], self.W[j]), dtype=np.int16)
            g[r0:r0 + MH, c0:c0 + MW] += np.int16(sgn) * mask[k]
        self.ver[j] += 1

    # -- suffix union: union_ge(j)[k] = OR(층 >= k 점유), (j, ver) 캐시 ---------

    def union_ge(self, j: int):
        cached = self._uge.get(j)
        if cached is not None and cached[0] == self.ver[j]:
            return cached[1]
        occ = self.occ[j]
        Kocc = (max(occ.keys()) + 1) if occ else 0
        out = [None] * Kocc
        acc = np.zeros((self.H[j], self.W[j]), dtype=np.int32)
        for k in range(Kocc - 1, -1, -1):
            g = occ.get(k)
            if g is not None:
                acc = acc | (g > 0).astype(np.int32)   # 새 배열(OR); 층별 객체 분리
            out[k] = acc
        self._uge[j] = (self.ver[j], out)
        return out

    @staticmethod
    def _dilate8(g):
        """0/1 그리드의 8-이웃(3x3) 이진 팽창. 원본 g를 각 방향으로 밀어 OR."""
        d = g.copy()
        d[1:, :] |= g[:-1, :]
        d[:-1, :] |= g[1:, :]
        d[:, 1:] |= g[:, :-1]
        d[:, :-1] |= g[:, 1:]
        d[1:, 1:] |= g[:-1, :-1]
        d[:-1, :-1] |= g[1:, 1:]
        d[1:, :-1] |= g[:-1, 1:]
        d[:-1, 1:] |= g[1:, :-1]
        return d

    def union_ge_touch(self, j: int):
        """union_ge의 8-이웃 팽창판, (j, ver) 캐시. scan은 이걸 점유로 써서
        '접촉(flush)도 충돌'로 본다 -- 정확 게이트의 NFP는 ray-cast 경계 처리가
        불안정해 정수 좌표 flush 접촉을 충돌로 치기도 한다(오목 hull이 변/점에서
        맞닿는 경우). 마스크는 접촉 시 인접 셀로 갈라져 disjoint가 되므로, 팽창으로
        1셀 접촉을 겹침으로 만들어 [scan-feasible ⇒ 게이트 통과]를 지킨다. 빈 셀이
        1칸이라도 있으면(≥1 gap) 팽창해도 안 겹치니 후보 손해는 flush에 국한된다."""
        cached = self._uge_dil.get(j)
        if cached is not None and cached[0] == self.ver[j]:
            return cached[1]
        out = [self._dilate8(v) for v in self.union_ge(j)]
        self._uge_dil[j] = (self.ver[j], out)
        return out

    # -- 전수 위치 스캔 ----------------------------------------------------------

    def scan(self, j: int, i: int, o: int):
        """bay j에서 (i, o)의 모든 정수 앵커 feasibility.

        반환 (feas (R, C) bool, mx0, my0): feas[r, c] True <=> 위치
        (x, y) = (c - mx0, r - my0)에 놓았을 때 마스크가 접촉-팽창 점유
        (union_ge_touch)와 disjoint (= 공간충돌 없음 + 크레인 진입 j>=k 가능 +
        flush 접촉 없음이 증명됨). 호출자가 IFP로 클립해야 컨테인먼트가 보장된다."""
        per_bay = self._scan.setdefault(j, {})
        cached = per_bay.get((i, o))
        if cached is not None and cached[0] == self.ver[j]:
            return cached[1], cached[2], cached[3]
        mask, mx0, my0 = self.mask(i, o)
        K, MH, MW = mask.shape
        R = self.H[j] - MH + 1
        C = self.W[j] - MW + 1
        if R <= 0 or C <= 0:
            feas = np.zeros((max(R, 0), max(C, 0)), dtype=bool)
        else:
            uge = self.union_ge_touch(j)
            total = np.zeros((R, C), dtype=np.int32)
            m32 = mask.astype(np.int32)
            for k in range(min(K, len(uge))):
                Vk = uge[k]
                if not Vk.any():
                    continue
                win = np.lib.stride_tricks.sliding_window_view(Vk, (MH, MW))
                total += np.einsum('rcij,ij->rc', win, m32[k])
            feas = (total == 0)
        per_bay[(i, o)] = (self.ver[j], feas, mx0, my0)
        return feas, mx0, my0

    # -- 접촉점수 셀 정렬 (인터록 패킹 레버, 플레이북 v13) ------------------------

    def contact_field(self, j: int):
        """(H+2, W+2) int32 접촉장: 테두리(=벽) 1, 내부는 층0 점유(>0)."""
        cached = self._field.get(j)
        if cached is not None and cached[0] == self.ver[j]:
            return cached[1]
        H, W = self.H[j], self.W[j]
        f = np.ones((H + 2, W + 2), dtype=np.int32)
        g = self.occ[j].get(0)
        inner = (g > 0).astype(np.int32) if g is not None \
            else np.zeros((H, W), dtype=np.int32)
        f[1:H + 1, 1:W + 1] = inner
        self._field[j] = (self.ver[j], f)
        return f

    def order_cells(self, j: int, i: int, o: int, feas, cap: int):
        """feas True 앵커를 접촉점수 내림차순(동점 bottom-left)으로 최대 cap개.
        점수 = 풋프린트 halo(4-이웃 둘레 셀)와 [벽 + 층0 점유]의 겹침 카운트."""
        rs, cs = np.nonzero(feas)
        if rs.size == 0:
            return []
        mask, mx0, my0 = self.mask(i, o)
        K, MH, MW = mask.shape
        fp = mask.any(axis=0).astype(np.int32)
        halo = np.zeros((MH + 2, MW + 2), dtype=np.int32)
        for dr, dc in ((0, 1), (2, 1), (1, 0), (1, 2)):
            halo[dr:dr + MH, dc:dc + MW] |= fp
        halo[1:MH + 1, 1:MW + 1] &= (1 - fp)
        field = self.contact_field(j)
        # field 패딩 1칸 = halo 확장 1칸이 상쇄 -> 앵커 (r, c)의 halo 좌상단은
        # field[r, c]에서 시작. 윈도 수 = (H+2)-(MH+2)+1 = R (feas와 정렬).
        win = np.lib.stride_tricks.sliding_window_view(field, (MH + 2, MW + 2))
        scores = np.einsum('rcij,ij->rc', win, halo)
        vals = scores[rs, cs]
        idx = np.lexsort((cs, rs, -vals))
        take = idx if cap is None else idx[:cap]
        return [(int(rs[t]), int(cs[t])) for t in take]
