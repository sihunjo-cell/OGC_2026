# Phase2/raster.py
"""Phase2.raster -- v12-계열 라스터 기하 엔진 (ALGORITHM_PLAYBOOK §3.3 이식).

soundness 계약(플레이북 §3.1): 마스크는 폴리곤의 superset(닫힌 단위 정사각형을
touch하면 1). 따라서 [마스크 disjoint ⇒ 실제 면적>0 겹침 불가]가 증명되고,
scan이 feasible이라 한 앵커는 '현재 상주 대비 공간충돌 없음 + 크레인 진입(j>=k)
가능'이 참이다. 반대 방향(마스크는 겹치지만 실제론 합법인 변-접촉)은 후보 하나
손해로만 나타난다. 시간축(내 EXIT 차단·역방향 차단)은 라스터가 증명하지 못하므로
호출자가 정확 게이트(crane_obstructed / crane_blocks_resident)를 통과시켜야 한다.

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
            hit = shapely.intersects(Polygon(ring), boxes)   # 경계 touch 포함 => superset
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
