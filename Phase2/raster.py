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

from ._diag import PROBE


class Raster:
    def __init__(self, prob_info: dict, pre, incremental: bool = True,
                 mask_share: bool = False):
        self.pre = pre
        self.n_bays = pre.n_bays
        self.W = [int(math.ceil(b["width"])) for b in prob_info["bays"]]
        self.H = [int(math.ceil(b["height"])) for b in prob_info["bays"]]
        self.occ = [dict() for _ in range(self.n_bays)]   # occ[j][k] -> int16 (H, W) 카운트
        self.ver = [0] * self.n_bays
        # (i, o) -> (uint8 (K, MH, MW), mx0, my0). 빌드 후 불변(읽기 전용)이라
        # mask_share=True면 pre에 붙여 설계도(Raster 인스턴스) 간 재사용해도 안전.
        if mask_share:
            cache = getattr(pre, "_raster_mask_cache", None)
            if cache is None:
                cache = {}
                pre._raster_mask_cache = cache
            self._mask = cache
        else:
            self._mask = {}
        self._uge = {}     # j -> (ver, [int32 (H, W)] 층별 suffix union)
        self._scan = {}    # j -> {(i, o): (ver, feas, mx0, my0)}
        self._field = {}   # j -> (ver, int32 (H+2, W+2) 접촉장, 테두리=벽)
        # 공간 국소 무효화용 변경영역 로그. _dirty[j][v] = 버전 v->v+1 스탬프의
        # 발자국 사각형(8이웃 팽창 1칸 포함, 격자 좌표로 클립). len == ver[j] 불변.
        self._incremental = incremental
        self._dirty = [[] for _ in range(self.n_bays)]

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
        if self._incremental:
            # 발자국 [r0, r0+MH) x [c0, c0+MW) 를 8이웃 팽창 1칸만큼 넓혀 사각형으로.
            # 포함 좌표계: 하한 -1, 상한 +MH/+MW (격자 경계 안으로 클립).
            fr0 = r0 - 1 if r0 > 0 else 0
            fc0 = c0 - 1 if c0 > 0 else 0
            fr1 = r0 + MH if r0 + MH < self.H[j] else self.H[j] - 1
            fc1 = c0 + MW if c0 + MW < self.W[j] else self.W[j] - 1
            self._dirty[j].append((fr0, fc0, fr1, fc1))
        if PROBE.enabled:
            PROBE.on_stamp(j, i, sgn, int(mask.any(axis=0).sum()),
                           self.H[j] * self.W[j])
        else:
            PROBE.on_stamp(j, i, sgn)

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
        PROBE.on_wholebay("점유합집합", float(self.H[j]) * self.W[j] * max(Kocc, 0))
        self._uge[j] = (self.ver[j], out)
        return out

    # -- 전수 위치 스캔 ----------------------------------------------------------

    def _affected_region(self, j, v0, v1, MH, MW, R, C):
        """v0 이후 ~ v1 까지의 스탬프가 (MH, MW) 블록의 어떤 앵커 feas를 바꿀 수
        있는지. 반환 (ar0, ar1, ac0, ac1) 반열린 앵커 범위 또는 None(안 바뀜).
        변경영역 사각형들의 합집합 D 를 구하고, 앵커 (r,c)의 윈도
        [r, r+MH) x [c, c+MW) 가 D 와 겹칠 수 있는 앵커 범위로 역산한다."""
        dirty = self._dirty[j]
        if v1 > len(dirty):          # 로그가 v1을 못 덮음(이론상 없음) -> 전체 재계산 신호
            return (0, R, 0, C)
        dr0 = dc0 = 1 << 30
        dr1 = dc1 = -1
        for (r0, c0, r1, c1) in dirty[v0:v1]:
            if r0 < dr0:
                dr0 = r0
            if c0 < dc0:
                dc0 = c0
            if r1 > dr1:
                dr1 = r1
            if c1 > dc1:
                dc1 = c1
        if dr1 < 0:                  # 이 범위에 스탬프 없음
            return None
        ar0 = dr0 - MH + 1
        ar0 = 0 if ar0 < 0 else ar0
        ar1 = dr1 + 1
        ar1 = R if ar1 > R else ar1
        ac0 = dc0 - MW + 1
        ac0 = 0 if ac0 < 0 else ac0
        ac1 = dc1 + 1
        ac1 = C if ac1 > C else ac1
        if ar0 >= ar1 or ac0 >= ac1:
            return None
        return (ar0, ar1, ac0, ac1)

    def scan(self, j: int, i: int, o: int):
        """bay j에서 (i, o)의 모든 정수 앵커 feasibility.

        반환 (feas (R, C) bool, mx0, my0): feas[r, c] True <=> 위치
        (x, y) = (c - mx0, r - my0)에 놓았을 때 마스크가 점유 suffix-union
        (union_ge)과 disjoint (= 공간충돌 없음 + 크레인 진입 j>=k 가능). 마스크는
        층별 hull superset이라 disjoint ⇒ 폴리곤 면적>0 겹침 없음(변 접촉=합법)이
        증명된다. 호출자가 IFP로 클립해야 컨테인먼트가 보장된다. 증분 모드에서는
        변경영역이 안 겹치면 캐시 그대로, 겹치면 그 앵커 범위만 다시 계산한다
        (전체 재계산과 비트 동일)."""
        per_bay = self._scan.setdefault(j, {})
        cached = per_bay.get((i, o))
        v1 = self.ver[j]
        if cached is not None and cached[0] == v1:
            PROBE.on_scan_hit(j, i, o)
            return cached[1], cached[2], cached[3]
        mask, mx0, my0 = self.mask(i, o)
        K, MH, MW = mask.shape
        R = self.H[j] - MH + 1
        C = self.W[j] - MW + 1

        # -- 증분 경로: 캐시가 있고 크기 유효할 때만 --------------------------
        if (self._incremental and cached is not None and R > 0 and C > 0
                and cached[1].shape == (R, C)):
            A = self._affected_region(j, cached[0], v1, MH, MW, R, C)
            if A is None:
                # 변경영역이 이 (i,o)의 어떤 앵커와도 안 겹침 -> 지도 불변.
                feas = cached[1]
                per_bay[(i, o)] = (v1, feas, mx0, my0)
                PROBE.on_scan_hit(j, i, o)
                return feas, mx0, my0
            ar0, ar1, ac0, ac1 = A
            if (ar1 - ar0) * (ac1 - ac0) < R * C:      # 진짜 부분일 때만
                feas = cached[1].copy()
                uge = self.union_ge(j)
                sub = np.zeros((ar1 - ar0, ac1 - ac0), dtype=np.int32)
                m32 = mask.astype(np.int32)
                for k in range(min(K, len(uge))):
                    Vk = uge[k]
                    if not Vk.any():
                        continue
                    win = np.lib.stride_tricks.sliding_window_view(
                        Vk[ar0:ar1 + MH - 1, ac0:ac1 + MW - 1], (MH, MW))
                    sub += np.einsum('rcij,ij->rc', win, m32[k])
                feas[ar0:ar1, ac0:ac1] = (sub == 0)
                per_bay[(i, o)] = (v1, feas, mx0, my0)
                PROBE.on_scan_miss(j, i, o, cached[0], v1, False,
                                   float(ar1 - ar0) * (ac1 - ac0) * MH * MW,
                                   int(feas.sum()))
                return feas, mx0, my0
            # A가 사실상 전체면 아래 전체 재계산으로 낙하

        # -- 전체 재계산 (콜드 / 비증분 / A=전체) ----------------------------
        _cached_ver = cached[0] if cached is not None else 0
        _cold = cached is None
        _cost = 0.0                      # einsum FLOP 프록시 (활성층 R*C*MH*MW 합)
        if R <= 0 or C <= 0:
            feas = np.zeros((max(R, 0), max(C, 0)), dtype=bool)
        else:
            uge = self.union_ge(j)
            total = np.zeros((R, C), dtype=np.int32)
            m32 = mask.astype(np.int32)
            for k in range(min(K, len(uge))):
                Vk = uge[k]
                if not Vk.any():
                    continue
                _cost += float(R) * C * MH * MW
                win = np.lib.stride_tricks.sliding_window_view(Vk, (MH, MW))
                total += np.einsum('rcij,ij->rc', win, m32[k])
            feas = (total == 0)
        if not _cold and cached[1].shape == feas.shape:
            PROBE.on_scan_diff(int(np.count_nonzero(feas != cached[1])), feas.size)
        per_bay[(i, o)] = (v1, feas, mx0, my0)
        PROBE.on_scan_miss(j, i, o, _cached_ver, v1, _cold, _cost,
                           int(feas.sum()))
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
        PROBE.on_wholebay("접촉장", float(H) * W)
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
