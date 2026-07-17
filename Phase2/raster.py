# Phase2/raster.py
"""라스터 기하 엔진: 배치가능 정수 앵커 전수 스캔 (soundness 계약 = hull-mask 메모리 원장)."""

from __future__ import annotations

import math
import os

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
        # (i,o) -> 마스크: 빌드 후 불변이라 pre 공유 안전
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
        self._cscan = {}   # j -> {(i, o): (ver, total, mx0, my0)} (nestle count 캐시)
        self._field = {}   # j -> (ver, 접촉장)
        # 증분 scan용 변경영역 로그 (규약 = ogc-code-invariants)
        self._incremental = incremental
        self._dirty = [[] for _ in range(self.n_bays)]
        # scan 캐시 바이트 상한 (초과 시 전량 clear = 비트동일)
        self._scan_cap = int(os.environ.get("OGC_SCAN_CAP_MB", "600")) * 1_000_000
        self._scan_bytes = 0

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
            # hull 래스터화 필수 (soundness 계약, 변경 금지 -- hull-mask 메모리 원장)
            hull = Polygon(ring).convex_hull
            hit = shapely.intersects(hull, boxes)   # 경계 touch 포함 => superset
            mask[k] = hit.reshape(MH, MW).astype(np.uint8)
        return mask, mx0, my0

    # -- 점유 스탬프 (카운트 그리드) --

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
            # 발자국 8이웃 1칸 팽창 사각형 기록
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

    # -- suffix union: union_ge(j)[k] = OR(층 >= k 점유) --

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
        """v0~v1 스탬프가 건드릴 앵커 범위(반열린) 또는 None(무영향)."""
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

    def _account(self, per_bay, key, feas):
        """scan 캐시 바이트 계정 (재저장 시 old 차감, 상한 초과 = 전량 clear)."""
        old = per_bay.get(key)
        if old is not None:
            self._scan_bytes -= old[1].nbytes
        self._scan_bytes += feas.nbytes
        if self._scan_bytes > self._scan_cap:
            for d in self._scan.values():
                d.clear()
            for d in self._cscan.values():   # count 캐시도 같은 예산에 포함
                d.clear()
            self._scan_bytes = feas.nbytes

    def scan(self, j: int, i: int, o: int):
        """(i,o) 전 앵커 feasibility (feas, mx0, my0) -- 호출자가 IFP 클립, 증분 캐시 비트동일."""
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

        # 증분 경로
        if (self._incremental and cached is not None and R > 0 and C > 0
                and cached[1].shape == (R, C)):
            A = self._affected_region(j, cached[0], v1, MH, MW, R, C)
            if A is None:
                # 변경영역이 이 (i,o)의 어떤 앵커와도 안 겹침 -> 지도 불변.
                feas = cached[1]
                self._account(per_bay, (i, o), feas)
                per_bay[(i, o)] = (v1, feas, mx0, my0)
                PROBE.on_scan_hit(j, i, o)
                return feas, mx0, my0
            ar0, ar1, ac0, ac1 = A
            if (ar1 - ar0) * (ac1 - ac0) < R * C:      # 부분일 때만
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
                self._account(per_bay, (i, o), feas)
                per_bay[(i, o)] = (v1, feas, mx0, my0)
                PROBE.on_scan_miss(j, i, o, cached[0], v1, False,
                                   float(ar1 - ar0) * (ac1 - ac0) * MH * MW,
                                   int(feas.sum()))
                return feas, mx0, my0
            # A가 사실상 전체면 전체 재계산으로 낙하

        # 전체 재계산 (콜드 / 비증분 / A=전체)
        _cached_ver = cached[0] if cached is not None else 0
        _cold = cached is None
        _cost = 0.0                      # einsum FLOP 프록시
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
        self._account(per_bay, (i, o), feas)
        per_bay[(i, o)] = (v1, feas, mx0, my0)
        PROBE.on_scan_miss(j, i, o, _cached_ver, v1, _cold, _cost,
                           int(feas.sum()))
        return feas, mx0, my0

    def count_scan(self, j: int, i: int, o: int):
        """앵커별 겹침 카운트 그리드 (nestle 후보용, scan과 동형 캐시; R/C<=0이면 None)."""
        per_bay = self._cscan.setdefault(j, {})
        cached = per_bay.get((i, o))
        v1 = self.ver[j]
        if cached is not None and cached[0] == v1:
            return cached[1], cached[2], cached[3]
        mask, mx0, my0 = self.mask(i, o)
        K, MH, MW = mask.shape
        R = self.H[j] - MH + 1
        C = self.W[j] - MW + 1
        if R <= 0 or C <= 0:
            return None, mx0, my0
        m32 = mask.astype(np.int32)
        if (self._incremental and cached is not None
                and cached[1].shape == (R, C)):
            A = self._affected_region(j, cached[0], v1, MH, MW, R, C)
            if A is None:
                total = cached[1]
                per_bay[(i, o)] = (v1, total, mx0, my0)
                return total, mx0, my0
            ar0, ar1, ac0, ac1 = A
            if (ar1 - ar0) * (ac1 - ac0) < R * C:
                total = cached[1].copy()
                uge = self.union_ge(j)
                sub = np.zeros((ar1 - ar0, ac1 - ac0), dtype=np.int32)
                for k in range(min(K, len(uge))):
                    Vk = uge[k]
                    if not Vk.any():
                        continue
                    win = np.lib.stride_tricks.sliding_window_view(
                        Vk[ar0:ar1 + MH - 1, ac0:ac1 + MW - 1], (MH, MW))
                    sub += np.einsum('rcij,ij->rc', win, m32[k])
                total[ar0:ar1, ac0:ac1] = sub
                self._account(per_bay, (i, o), total)
                per_bay[(i, o)] = (v1, total, mx0, my0)
                return total, mx0, my0
        uge = self.union_ge(j)
        total = np.zeros((R, C), dtype=np.int32)
        for k in range(min(K, len(uge))):
            Vk = uge[k]
            if not Vk.any():
                continue
            win = np.lib.stride_tricks.sliding_window_view(Vk, (MH, MW))
            total += np.einsum('rcij,ij->rc', win, m32[k])
        self._account(per_bay, (i, o), total)
        per_bay[(i, o)] = (v1, total, mx0, my0)
        return total, mx0, my0

    # -- 접촉점수 셀 정렬 --

    def contact_field(self, j: int):
        """접촉장 (H+2, W+2): 테두리=벽 1, 내부=층0 점유."""
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

    def order_cells(self, j: int, i: int, o: int, feas, cap: int,
                    futures=None, frag_w: float = 0.0):
        """앵커를 접촉점수 내림차순 cap개로 (frag_w>0 = ΔF 페널티 결합, 공식은 invariants 원장)."""
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
        # field 패딩 1칸 = halo 확장 1칸 상쇄 (앵커 정렬)
        win = np.lib.stride_tricks.sliding_window_view(field, (MH + 2, MW + 2))
        scores = np.einsum('rcij,ij->rc', win, halo)
        vals = scores[rs, cs]
        if frag_w > 0.0 and futures:
            pen = np.zeros(rs.size, dtype=np.float64)
            for (_m, MHm, MWm, sat, tot) in futures:
                Rm, Cm = sat.shape[0] - 1, sat.shape[1] - 1
                # bbox 교차 앵커 창 (반열린)
                r0 = np.clip(rs - MHm + 1, 0, Rm)
                r1 = np.clip(rs + MH, 0, Rm)
                c0 = np.clip(cs - MWm + 1, 0, Cm)
                c1 = np.clip(cs + MW, 0, Cm)
                kill = (sat[r1, c1] - sat[r0, c1] - sat[r1, c0] + sat[r0, c0])
                pen += kill.astype(np.float64) / (tot + 1.0)
            vals = vals - frag_w * pen
        idx = np.lexsort((cs, rs, -vals))
        take = idx if cap is None else idx[:cap]
        return [(int(rs[t]), int(cs[t])) for t in take]

    @staticmethod
    def sat_of(allow) -> "np.ndarray":
        """적분영상 (H+1, W+1): sat[a, b] = allow[:a, :b] 합."""
        H, W = allow.shape
        sat = np.zeros((H + 1, W + 1), dtype=np.int32)
        np.cumsum(np.cumsum(allow, axis=0, dtype=np.int32), axis=1,
                  out=sat[1:, 1:])
        return sat
