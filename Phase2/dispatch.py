# Phase2/dispatch.py
"""Phase2.dispatch -- 이벤트 구동 ATC 디스패처("the pump").

bay 배정(Z2/Z3)은 고정하고 ENTRY/EXIT 타이밍과 (x,y,o) 배치만 시간순으로 결정.
이벤트(release + 예정 exit)마다: ① exit 제거 ② release 큐잉 ③ ATC 우선순위 admission
(raster.scan 앵커 → IFP 클립 → 접촉순 셀 → 시간축 정확 게이트). 같은 tick은 EXIT
먼저(핸드오프). 미배치 잔여는 force_place로 완결(출력은 항상 완전한 feasible 배정)."""

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
    """반환 (coords, orient, entry, exit_, forced_cons, bay).

    동적 bay(dispatch_dynamic_bay)가 admission 실패 시 급한 블록을 다른 bay로
    재라우팅할 수 있어 bay가 입력과 달라질 수 있으므로 최종 bay도 함께 반환한다."""
    blocks = prob_info["blocks"]
    n = len(blocks)
    P = [b["processing_time"] for b in blocks]
    D = [b["due_date"] for b in blocks]
    R = [b["release_time"] for b in blocks]
    bay = list(p1_out.bay)

    dyn_bay = bool(getattr(cfg, "dispatch_dynamic_bay", True))
    # 동적 bay: 블록별 배치가능(IFP 유효) 후보 bay 집합을 사전계산(선호순 = 동점 tiebreak;
    # 실제 라우팅은 admission 실패 시점에 least-util 순으로 재정렬해 선택).
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
                    mask_share=bool(getattr(cfg, "mask_cache_share", False)))
    fail_stop = int(getattr(cfg, "dispatch_admit_fail_stop", 0) or 0)
    pbar = (sum(P) / n) if n else 1.0
    amin = [min(pre.area[i]) for i in range(n)]
    abar = (sum(amin) / n) if n else 1.0
    # 동적 bay: 부하 균형용 bay별 점유면적/용량 추적(least-util bay로 라우팅).
    bay_area = bay_occ = None
    reroute_guard = bool(getattr(cfg, "dispatch_reroute_guard", False))
    g_w1 = g_w3 = 1.0
    S = None
    if dyn_bay:
        _bd = prob_info["bays"]
        bay_area = [max(1.0, _bd[j]["width"] * _bd[j]["height"])
                    for j in range(pre.n_bays)]
        bay_occ = [0.0] * pre.n_bays
        if reroute_guard:
            _w = prob_info.get("weights", {})
            g_w1 = float(_w.get("w1", 1.0))
            g_w3 = float(_w.get("w3", 1.0))
            S = [b["bay_preferences"] for b in blocks]
    kappa = max(1e-9, float(cfg.atc_kappa))
    alpha = float(cfg.atc_alpha)

    coords: dict = {}
    orient: dict = {}
    entry = list(p1_out.entry)
    exit_ = list(p1_out.exit_)
    placed = [[] for _ in range(pre.n_bays)]   # bay별 현재 상주(커밋 & 아직 exit 전)
    queue = [[] for _ in range(pre.n_bays)]    # bay별 released & 미배치
    forced_cons: set = set()

    def _prio(i, t):
        # ATC (플레이북 §6): 1/((anorm^alpha) * p) * exp(-max(0, slack)/(kappa*pbar))
        anorm = (amin[i] / abar) if abar > 0 else 1.0
        slack = D[i] - P[i] - t
        return (1.0 / ((anorm ** alpha) * max(1, P[i]))) \
            * math.exp(-max(0.0, slack) / (kappa * pbar))

    def _exact_gate(i, j, o, pos, xt):
        # scan이 증명 못 하는 시간축 두 가지만 정확 검사:
        #  (a) 내 체류 중 exit하는 상주의 반출을 내가 막는가 (역방향 차단)
        #  (b) 내 exit(xt) 시점 잔류 상주가 내 반출을 막는가
        # 경계(exit == xt)는 양쪽 모두에 포함 = 이중 보수 (id tie-break 미러 회피).
        for k in placed[j]:
            if exit_[k] <= xt and crane_blocks_resident(i, o, pos, k, coords, orient, pre):
                return False
        stayers = [k for k in placed[j] if exit_[k] >= xt]
        if stayers and crane_obstructed(i, o, pos, stayers, coords, orient, pre):
            return False
        return True

    def _try_admit(i, j, t, rank=0, earlier=0):
        xt = t + P[i]
        cap = (cfg.dispatch_cand_cap_hi if len(queue[j]) >= cfg.dispatch_queue_hi
               else cfg.dispatch_cand_cap)
        any_space = False          # 진단: 어떤 방향이라도 IFP∩feasible 앵커>0 였나
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
            for (r, c) in raster.order_cells(j, i, o, allow, cap):
                cells_tried += 1
                pos = (int(c) - mx0, int(r) - my0)
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
        # ① t까지의 exit 반영 (같은 tick EXIT-먼저 = 핸드오프 슬롯 사용)
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
        # ③ ATC 순서 admission (결정론: 동점은 낮은 id)
        PROBE.set_ctx(t, "admit")
        for j in range(pre.n_bays):
            if not queue[j]:
                continue
            _earlier = 0     # 진단: 같은 tick·bay 에서 앞서 admit 된 수 (캐스케이드 깊이)
            _fails = 0       # 마지막 admit 이후 연속 실패 수 (조기중단 카운터)
            for _rank, i in enumerate(sorted(queue[j], key=lambda b: (-_prio(b, t), b))):
                if deadline is not None and time.perf_counter() >= deadline:
                    break
                if _try_admit(i, j, t, _rank, _earlier):
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
                    # 조건부: 지금 넣어도 지각(t+P>D)인 급한 블록만 다른 bay로 admit-now.
                    # 여유 블록은 제 bay 대기(비혼잡 오라우팅 회귀 방지).
                    if dyn_bay and t + P[i] > D[i]:
                        pool = (b for b in elig_bays[i] if b != j)
                        if reroute_guard:
                            # 목적-aware 가드: 선호손실(Z3)이 이미 확정된 지각비용을
                            # 넘는 bay 제외. 지각이 클수록 더 비선호 bay가 해금.
                            _late = t + P[i] - D[i]
                            pool = [b for b in pool
                                    if g_w3 * (S[i][j] - S[i][b]) <= g_w1 * _late]
                        # 허용된 bay 중 가장 여유있는(least-util) 순으로 시도.
                        cands = sorted(pool, key=lambda b: bay_occ[b] / bay_area[b])
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
                    # 마지막 admit 이후 F회 연속 실패 -> 남은 큐는 다음 이벤트로 이월.
                    if fail_stop and _fails >= fail_stop:
                        break

    # -- 잔여(마감 초과 포함) -> 빈 창 강제 배치: 출력은 항상 완전한 배정 --------
    def _force(i):
        j = bay[i]
        others = [(entry[k], exit_[k]) for k in range(n)
                  if k in coords and bay[k] == j and k != i]
        pos, o, e, x = rp.force_place(i, j, others, pre, R, P)
        coords[i], orient[i] = pos, o
        entry[i], exit_[i] = e, x
        forced_cons.add(i)

    for j in range(pre.n_bays):
        for i in list(queue[j]):
            _force(i)
    for i in range(n):
        if i not in coords:      # 방어적 (이벤트 누락 등)
            _force(i)
    return coords, orient, entry, exit_, forced_cons, bay
