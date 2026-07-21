# Phase2/dispatch.py
"""이벤트 구동 ATC 디스패처: bay 배정 고정, 배치·타이밍을 시간순 결정(잔여는 강제 완결)."""

from __future__ import annotations

import heapq
import math
import time

import numpy as np

from . import repair as rp
from ._diag import PROBE
from .crane import crane_blocks_resident, crane_obstructed
from .raster import Raster


def dispatch_construct(prob_info: dict, p1_out, pre, cfg, deadline=None):
    """반환 (coords, orient, entry, exit_, forced_cons, bay) -- bay는 재라우팅 반영 최종값."""
    blocks = prob_info["blocks"]
    n = len(blocks)
    P = [b["processing_time"] for b in blocks]
    D = [b["due_date"] for b in blocks]
    R = [b["release_time"] for b in blocks]
    bay = list(p1_out.bay)

    dyn_bay = bool(getattr(cfg, "dispatch_dynamic_bay", True))
    # 블록별 eligible bay 사전계산(선호순; 라우팅 시 least-util로 재정렬)
    elig_bays = None
    if dyn_bay:
        elig_bays = [[] for _ in range(n)]
        for i in range(n):
            pf = blocks[i].get("bay_preferences", [])
            cand = []
            for j2 in range(pre.n_bays):
                for o in range(len(pre.poly[i])):
                    (xl, xh), (yl, yh) = pre.IFP[i][o][j2]
                    if xl <= xh and yl <= yh:
                        cand.append(j2)
                        break
            cand.sort(key=lambda j2: -(pf[j2] if j2 < len(pf) else 0))
            elig_bays[i] = cand
    raster = Raster(prob_info, pre,
                    incremental=bool(getattr(cfg, "scan_incremental", True)),
                    mask_share=bool(getattr(cfg, "mask_cache_share", False)),
                    morph=bool(getattr(cfg, "scan_morph", False)))
    fail_stop = int(getattr(cfg, "dispatch_admit_fail_stop", 0) or 0)
    # ΔF 파편화 항 (FLOP 캡 초과 시 결정론적 셧오프)
    frag_w = float(getattr(cfg, "dispatch_fragdelta", 0.0) or 0.0)
    frag_q = int(getattr(cfg, "fragdelta_q", 4) or 0)
    frag_cap = float(getattr(cfg, "fragdelta_flop_cap", 2e9))
    frag_dens = float(getattr(cfg, "fragdelta_dens_hi", 0.0) or 0.0)
    frag_flops, frag_alive = 0.0, True
    def _dens0(j):
        # bay j의 layer-0 점유밀도 (ΔF 형성기 게이트용)
        g = raster.occ[j].get(0)
        if g is None:
            return 0.0
        return float(np.count_nonzero(g)) / g.size
    # hull-nestle 회수 (mask 전멸 시 정밀 재검사)
    nes_k = int(getattr(cfg, "dispatch_nestle_k", 0) or 0)
    nes_cap = int(getattr(cfg, "dispatch_nestle_cap", 12) or 0)
    nes_flop_cap = float(getattr(cfg, "dispatch_nestle_flop_cap", 2e9))
    nes_flops, nes_alive = 0.0, True
    # near-main 합류: 얕은-겹침 앵커를 main-pass 접촉-순위 경쟁에 (issue/08)
    nm_k = int(getattr(cfg, "dispatch_nearmain_k", 0) or 0)
    nm_cap = int(getattr(cfg, "dispatch_nearmain_cap", 16) or 0)
    nm_dens = float(getattr(cfg, "dispatch_nearmain_dens_hi", 0.0) or 0.0)
    nm_flop_cap = float(getattr(cfg, "dispatch_nearmain_flop_cap", 2e10))
    nm_flops, nm_alive = 0.0, True
    # 결정-플립 (형제 궤적 재시작용)
    flip_call = int(getattr(cfg, "dispatch_flip_call", 0) or 0)
    # orient-합동 순위 (전 orientation 후보 접촉점수 병합 -> 전역 순위 admit)
    orient_joint = bool(getattr(cfg, "dispatch_orient_joint", False))
    oc_n = 0
    nes_space = None
    if nes_k > 0 or nm_k > 0:
        from .nestle import ExactSpace
        nes_space = ExactSpace(pre, fast=bool(
            getattr(cfg, "dispatch_nestle_fast", True)))
    pbar = (sum(P) / n) if n else 1.0
    amin = [min(pre.area[i]) for i in range(n)]
    # 라우팅용 bay별 점유/용량 추적
    bay_area = bay_occ = None
    if dyn_bay:
        _bd = prob_info["bays"]
        bay_area = [max(1.0, _bd[j]["width"] * _bd[j]["height"])
                    for j in range(pre.n_bays)]
        bay_occ = [0.0] * pre.n_bays
    kappa = max(1e-9, float(cfg.atc_kappa))
    # 게이트 쌍판정 memo (판정-동치 -- 근거·한계는 issue/05 Results)
    gate_memo: dict = {}
    gate_omemo: dict = {}

    coords: dict = {}
    orient: dict = {}
    entry = list(p1_out.entry)
    exit_ = list(p1_out.exit_)
    placed = [[] for _ in range(pre.n_bays)]   # bay별 현재 상주(커밋 & 아직 exit 전)
    queue = [[] for _ in range(pre.n_bays)]    # bay별 released & 미배치
    forced_cons: set = set()

    def _prio(i, t):
        # ATC: 1/P * exp(-max(0, slack)/(kappa*pbar))
        slack = D[i] - P[i] - t
        return (1.0 / max(1, P[i])) * math.exp(-max(0.0, slack) / (kappa * pbar))

    # in-bay 순서 힌트: hint 있는 블록을 release tick 내 먼저 admit (None=순수 ATC=동일)
    order_hint = getattr(cfg, "dispatch_order_hint", None) or {}

    def _order_key(i, t):
        h = order_hint.get(i) if hasattr(order_hint, "get") else None
        return (0, h, i) if h is not None else (1, -_prio(i, t), i)

    def _exact_gate(i, j, o, pos, xt):
        # 시간축 crane 검사(역방향+내 exit). 경계 규칙은 메모리 ogc-code-invariants 참조.
        # 배치 좌표는 디코드 내 write-once -> 쌍판정은 순수 = 디코드-스코프 memo 유효.
        for k in placed[j]:
            if exit_[k] <= xt:
                key = (i, o, pos, k)
                v = gate_memo.get(key)
                if v is None:
                    v = crane_blocks_resident(i, o, pos, k, coords, orient, pre)
                    if len(gate_memo) < 200000:
                        gate_memo[key] = v
                if v:
                    return False
        stayers = [k for k in placed[j] if exit_[k] >= xt]
        if stayers:
            key = (i, o, pos, tuple(stayers))
            v = gate_omemo.get(key)
            if v is None:
                v = crane_obstructed(i, o, pos, stayers, coords, orient, pre)
                if len(gate_omemo) < 200000:
                    gate_omemo[key] = v
            if v:
                return False
        return True

    def _try_nestle(i, j, t, xt):
        # mask-비가시 합법 앵커 회수: 0<count<=K를 count 오름차순 cap개 정밀 재검사
        nonlocal nes_flops, nes_alive
        for o in range(len(pre.poly[i])):
            if deadline is not None and time.perf_counter() >= deadline:
                return False
            (x_lo, x_hi), (y_lo, y_hi) = pre.IFP[i][o][j]
            if x_lo > x_hi or y_lo > y_hi:
                continue
            msk, _, _ = raster.mask(i, o)
            Kq, MH, MW = msk.shape
            proxy = (float(max(raster.H[j] - MH + 1, 0))
                     * max(raster.W[j] - MW + 1, 0) * MH * MW * Kq)
            if proxy <= 0.0:
                continue
            if nes_flops + proxy > nes_flop_cap:
                nes_alive = False
                return False
            nes_flops += proxy
            total, mx0, my0 = raster.count_scan(j, i, o)
            if total is None:
                continue
            r_lo, r_hi = max(0, y_lo + my0), min(total.shape[0] - 1, y_hi + my0)
            c_lo, c_hi = max(0, x_lo + mx0), min(total.shape[1] - 1, x_hi + mx0)
            if r_lo > r_hi or c_lo > c_hi:
                continue
            sub = total[r_lo:r_hi + 1, c_lo:c_hi + 1]
            pos_rc = np.argwhere((sub > 0) & (sub <= nes_k))
            if pos_rc.size == 0:
                continue
            vals = sub[pos_rc[:, 0], pos_rc[:, 1]]
            for oi in np.argsort(vals, kind="stable")[:nes_cap]:
                r, c = pos_rc[oi]
                pos = (int(c + c_lo) - mx0, int(r + r_lo) - my0)
                if not nes_space.space_ok(j, raster.ver[j], i, o, pos,
                                          placed[j], coords, orient):
                    continue
                if _exact_gate(i, j, o, pos, xt):
                    coords[i] = pos
                    orient[i] = o
                    entry[i] = t
                    exit_[i] = xt
                    raster.add(j, i, o, pos)
                    placed[j].append(i)
                    return True
        return False

    def _try_admit(i, j, t, rank=0, earlier=0, futures=None):
        nonlocal nm_flops, nm_alive, oc_n
        # 마감 후 스캔 미진입
        if deadline is not None and time.perf_counter() >= deadline:
            return False
        xt = t + P[i]
        cap = (cfg.dispatch_cand_cap_hi if len(queue[j]) >= cfg.dispatch_queue_hi
               else cfg.dispatch_cand_cap)
        # ΔF 미래 표적 (자기 자신 제외)
        futs = None
        if futures:
            futs = [f for f in futures if f[0] != i] or None
        if orient_joint and len(pre.poly[i]) > 1:
            return _admit_joint(i, j, t, xt, cap, futs, rank, earlier)
        any_space = False
        feas_anchors = cells_tried = gate_rej = 0
        for o in range(len(pre.poly[i])):
            (x_lo, x_hi), (y_lo, y_hi) = pre.IFP[i][o][j]
            if x_lo > x_hi or y_lo > y_hi:
                continue
            feas, mx0, my0 = raster.scan(j, i, o)
            if feas.size == 0:
                continue
            allow = np.zeros_like(feas)
            r_lo, r_hi = max(0, y_lo + my0), min(feas.shape[0] - 1, y_hi + my0)
            c_lo, c_hi = max(0, x_lo + mx0), min(feas.shape[1] - 1, x_hi + mx0)
            if r_lo > r_hi or c_lo > c_hi:
                continue
            allow[r_lo:r_hi + 1, c_lo:c_hi + 1] = feas[r_lo:r_hi + 1, c_lo:c_hi + 1]
            n_ok = int(allow.sum())
            if n_ok:
                any_space = True
                feas_anchors += n_ok
            # near-main: 얕은-겹침 후보를 같은 접촉-순위에 합류 (near 후보만 exact 검사)
            grid, budget, near = allow, cap, None
            if nm_k > 0 and nm_alive \
                    and (nm_dens <= 0.0 or _dens0(j) < nm_dens):
                _cs = raster._cscan.get(j, {}).get((i, o))
                if _cs is None or _cs[0] != raster.ver[j]:
                    _mk, _, _ = raster.mask(i, o)
                    _Kq, _MHq, _MWq = _mk.shape
                    proxy = (float(max(raster.H[j] - _MHq + 1, 0))
                             * max(raster.W[j] - _MWq + 1, 0) * _MHq * _MWq * _Kq)
                    if nm_flops + proxy > nm_flop_cap:
                        nm_alive = False
                    else:
                        nm_flops += proxy
                if nm_alive:
                    total, _, _ = raster.count_scan(j, i, o)
                    if total is not None:
                        near = np.zeros_like(allow)
                        _sub = total[r_lo:r_hi + 1, c_lo:c_hi + 1]
                        near[r_lo:r_hi + 1, c_lo:c_hi + 1] = \
                            (_sub > 0) & (_sub <= nm_k)
                        if near.any():
                            grid = allow | near
                            budget = cap + nm_cap
                        else:
                            near = None
            cells = raster.order_cells(j, i, o, grid, budget, futs, frag_w)
            if cells:
                oc_n += 1
                if oc_n == flip_call and len(cells) > 1:
                    cells = cells[1:] + cells[:1]
            for (r, c) in cells:
                cells_tried += 1
                pos = (int(c) - mx0, int(r) - my0)
                if near is not None and not allow[r, c]:
                    if not nes_space.space_ok(j, raster.ver[j], i, o, pos,
                                              placed[j], coords, orient):
                        gate_rej += 1
                        continue
                if _exact_gate(i, j, o, pos, xt):
                    coords[i] = pos
                    orient[i] = o
                    entry[i] = t
                    exit_[i] = xt
                    raster.add(j, i, o, pos)
                    placed[j].append(i)
                    PROBE.on_attempt(t, j, i, rank, earlier, "admitted",
                                     feas_anchors, cells_tried, gate_rej)
                    return True
                gate_rej += 1
        # hull-nestle 회수: mask 패스 전멸 시에만
        if nes_k > 0 and nes_alive and _try_nestle(i, j, t, xt):
            PROBE.on_attempt(t, j, i, rank, earlier, "admitted",
                             feas_anchors, cells_tried, gate_rej)
            return True
        PROBE.on_attempt(t, j, i, rank, earlier,
                         "gate_fail" if any_space else "no_space",
                         feas_anchors, cells_tried, gate_rej)
        return False

    def _admit_joint(i, j, t, xt, cap, futs, rank, earlier):
        """orient-합동 순위: 전 orientation 후보를 접촉점수로 병합해 전역 순위로 admit.
        placement마다 동일 _exact_gate/space_ok 통과 = soundness 불변, 순서만 병합."""
        nonlocal nm_flops, nm_alive, oc_n
        pool = []
        meta = {}
        any_space = False
        feas_anchors = 0
        for o in range(len(pre.poly[i])):
            (x_lo, x_hi), (y_lo, y_hi) = pre.IFP[i][o][j]
            if x_lo > x_hi or y_lo > y_hi:
                continue
            feas, mx0, my0 = raster.scan(j, i, o)
            if feas.size == 0:
                continue
            r_lo, r_hi = max(0, y_lo + my0), min(feas.shape[0] - 1, y_hi + my0)
            c_lo, c_hi = max(0, x_lo + mx0), min(feas.shape[1] - 1, x_hi + mx0)
            if r_lo > r_hi or c_lo > c_hi:
                continue
            allow = np.zeros_like(feas)
            allow[r_lo:r_hi + 1, c_lo:c_hi + 1] = feas[r_lo:r_hi + 1, c_lo:c_hi + 1]
            n_ok = int(allow.sum())
            if n_ok:
                any_space = True
                feas_anchors += n_ok
            grid, budget, near = allow, cap, None
            if nm_k > 0 and nm_alive \
                    and (nm_dens <= 0.0 or _dens0(j) < nm_dens):
                _cs = raster._cscan.get(j, {}).get((i, o))
                if _cs is None or _cs[0] != raster.ver[j]:
                    _mk, _, _ = raster.mask(i, o)
                    _Kq, _MHq, _MWq = _mk.shape
                    proxy = (float(max(raster.H[j] - _MHq + 1, 0))
                             * max(raster.W[j] - _MWq + 1, 0) * _MHq * _MWq * _Kq)
                    if nm_flops + proxy > nm_flop_cap:
                        nm_alive = False
                    else:
                        nm_flops += proxy
                if nm_alive:
                    total, _, _ = raster.count_scan(j, i, o)
                    if total is not None:
                        near = np.zeros_like(allow)
                        _sub = total[r_lo:r_hi + 1, c_lo:c_hi + 1]
                        near[r_lo:r_hi + 1, c_lo:c_hi + 1] = \
                            (_sub > 0) & (_sub <= nm_k)
                        if near.any():
                            grid = allow | near
                            budget = cap + nm_cap
                        else:
                            near = None
            cells = raster.order_cells(j, i, o, grid, budget, futs, frag_w,
                                       with_vals=True)
            if cells:
                oc_n += 1
                if oc_n == flip_call and len(cells) > 1:
                    cells = cells[1:] + cells[:1]
            meta[o] = (allow, near, mx0, my0)
            for (r, c, v) in cells:
                pool.append((v, o, r, c, near is not None and not allow[r, c]))
        if not pool:
            if nes_k > 0 and nes_alive and _try_nestle(i, j, t, xt):
                PROBE.on_attempt(t, j, i, rank, earlier, "admitted",
                                 feas_anchors, 0, 0)
                return True
            PROBE.on_attempt(t, j, i, rank, earlier,
                             "gate_fail" if any_space else "no_space",
                             feas_anchors, 0, 0)
            return False
        # 전역 접촉점수 내림차순 (동점 tie-break = order_cells와 동형: r, c; 안정정렬 = orient순)
        pool.sort(key=lambda e: (-e[0], e[2], e[3]))
        cells_tried = gate_rej = 0
        for (v, o, r, c, is_near) in pool:
            cells_tried += 1
            allow, near, mx0, my0 = meta[o]
            pos = (int(c) - mx0, int(r) - my0)
            if is_near:
                if not nes_space.space_ok(j, raster.ver[j], i, o, pos,
                                          placed[j], coords, orient):
                    gate_rej += 1
                    continue
            if _exact_gate(i, j, o, pos, xt):
                coords[i] = pos
                orient[i] = o
                entry[i] = t
                exit_[i] = xt
                raster.add(j, i, o, pos)
                placed[j].append(i)
                PROBE.on_attempt(t, j, i, rank, earlier, "admitted",
                                 feas_anchors, cells_tried, gate_rej)
                return True
            gate_rej += 1
        if nes_k > 0 and nes_alive and _try_nestle(i, j, t, xt):
            PROBE.on_attempt(t, j, i, rank, earlier, "admitted",
                             feas_anchors, cells_tried, gate_rej)
            return True
        PROBE.on_attempt(t, j, i, rank, earlier,
                         "gate_fail" if any_space else "no_space",
                         feas_anchors, cells_tried, gate_rej)
        return False

    # -- 이벤트 루프 -----------------------------------------------------------
    order = sorted(range(n), key=lambda i: (R[i], i))
    ri = 0
    ev = sorted({int(R[i]) for i in range(n)})
    heapq.heapify(ev)
    in_ev = set(ev)

    while ev:
        t = heapq.heappop(ev)
        in_ev.discard(t)
        PROBE.set_ctx(t, "exit")
        # ① exit 반영 (같은 tick EXIT-먼저)
        for j in range(pre.n_bays):
            keep = []
            for k in placed[j]:
                if exit_[k] <= t:
                    raster.remove(j, k, orient[k], coords[k])
                    if dyn_bay:
                        bay_occ[j] -= amin[k]
                else:
                    keep.append(k)
            placed[j] = keep
        # ② release 반영
        while ri < n and R[order[ri]] <= t:
            b = order[ri]
            queue[bay[b]].append(b)
            ri += 1
        if deadline is not None and time.perf_counter() >= deadline:
            break
        # ③ ATC 순서 admission (동점 = 낮은 id)
        PROBE.set_ctx(t, "admit")
        for j in range(pre.n_bays):
            if not queue[j]:
                continue
            # ΔF pre-pass: M* feasible 지도 SAT를 패스당 1회 구축(패스 중 낡아도 사용)
            futures = None
            if frag_w > 0.0 and frag_alive and frag_q > 0 \
                    and len(queue[j]) >= int(getattr(cfg, "fragdelta_queue_hi", 6)) \
                    and (frag_dens <= 0.0 or _dens0(j) < frag_dens):
                pool = list(queue[j])
                futures = []
                for m in sorted(pool, key=lambda b: (-amin[b], b))[:frag_q]:
                    if deadline is not None and time.perf_counter() >= deadline:
                        break
                    o_m = min(range(len(pre.poly[m])),
                              key=lambda oo: pre.area[m][oo])
                    (xl, xh), (yl, yh) = pre.IFP[m][o_m][j]
                    if xl > xh or yl > yh:
                        continue
                    msk, _, _ = raster.mask(m, o_m)
                    Km, MHm, MWm = msk.shape
                    cached = raster._scan.get(j, {}).get((m, o_m))
                    if cached is None or cached[0] != raster.ver[j]:
                        # 신규/낡은 스캔만 전체-재계산 상한으로 계정(보수적)
                        proxy = (float(max(raster.H[j] - MHm + 1, 0))
                                 * max(raster.W[j] - MWm + 1, 0) * MHm * MWm * Km)
                        if frag_flops + proxy > frag_cap:
                            frag_alive = False
                            break
                        frag_flops += proxy
                    feas_m, mxm, mym = raster.scan(j, m, o_m)
                    if feas_m.size == 0:
                        continue
                    r_lo = max(0, yl + mym)
                    r_hi = min(feas_m.shape[0] - 1, yh + mym)
                    c_lo = max(0, xl + mxm)
                    c_hi = min(feas_m.shape[1] - 1, xh + mxm)
                    if r_lo > r_hi or c_lo > c_hi:
                        continue
                    allow_m = np.zeros_like(feas_m)
                    allow_m[r_lo:r_hi + 1, c_lo:c_hi + 1] = \
                        feas_m[r_lo:r_hi + 1, c_lo:c_hi + 1]
                    tot = int(allow_m.sum())
                    if tot == 0:
                        continue
                    futures.append((m, MHm, MWm, Raster.sat_of(allow_m), tot))
                if not futures:
                    futures = None
            _earlier = 0     # 같은 pass 내 선행 admit 수 (진단)
            _fails = 0       # 마지막 admit 이후 연속 실패 (fail_stop 카운터)
            for _rank, i in enumerate(sorted(queue[j], key=lambda b: _order_key(b, t))):
                if deadline is not None and time.perf_counter() >= deadline:
                    break
                if _try_admit(i, j, t, _rank, _earlier, futures):
                    _earlier += 1
                    _fails = 0
                    queue[j].remove(i)
                    if dyn_bay:
                        bay_occ[j] += amin[i]
                    x = int(exit_[i])
                    if x not in in_ev:
                        heapq.heappush(ev, x)
                        in_ev.add(x)
                else:
                    admitted_elsewhere = False
                    # 지각 확정(t+P>D) 블록만 least-util 타 bay로 admit-now
                    if dyn_bay and t + P[i] > D[i]:
                        cands = sorted((b for b in elig_bays[i] if b != j),
                                       key=lambda b: bay_occ[b] / bay_area[b])
                        for j2 in cands:
                            if _try_admit(i, j2, t, _rank, _earlier):
                                bay[i] = j2
                                bay_occ[j2] += amin[i]
                                queue[j].remove(i)
                                x = int(exit_[i])
                                if x not in in_ev:
                                    heapq.heappush(ev, x)
                                    in_ev.add(x)
                                admitted_elsewhere = True
                                _fails = 0
                                break
                    if admitted_elsewhere:
                        continue
                    _fails += 1
                    # F회 연속 실패 -> 남은 큐 이월
                    if fail_stop and _fails >= fail_stop:
                        break

    # -- 잔여 강제 배치 (per-bay 스케줄 스냅숏은 coords 기준 -- 규약은 ogc-code-invariants)
    _sched = [[] for _ in range(pre.n_bays)]
    _tail = [0] * pre.n_bays
    for k in coords:
        _sched[bay[k]].append((entry[k], exit_[k]))
        if exit_[k] > _tail[bay[k]]:
            _tail[bay[k]] = exit_[k]

    def _force(i):
        j = bay[i]
        if deadline is not None and time.perf_counter() >= deadline:
            # 마감 후: 빈-창 탐색 대신 O(1) tail-pointer
            pos, o = rp.force_corner(i, j, pre)
            e = max(int(R[i]), _tail[j])
            x = e + P[i]
        else:
            pos, o, e, x = rp.force_place(i, j, _sched[j], pre, R, P)
        coords[i], orient[i] = pos, o
        entry[i], exit_[i] = e, x
        _sched[j].append((e, x))
        if x > _tail[j]:
            _tail[j] = x
        forced_cons.add(i)

    for j in range(pre.n_bays):
        for i in list(queue[j]):
            _force(i)
    for i in range(n):
        if i not in coords:      # 방어적 (이벤트 누락 등)
            _force(i)
    return coords, orient, entry, exit_, forced_cons, bay
