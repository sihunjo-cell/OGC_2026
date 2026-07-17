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
from .collision import _collision_free
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
    # ΔF 항(issue/05): FLOP 프록시 누적 상한 초과 시 잔여 결정론적 셧오프(비트동일).
    frag_w = float(getattr(cfg, "dispatch_fragdelta", 0.0) or 0.0)
    frag_q = int(getattr(cfg, "fragdelta_q", 4) or 0)
    frag_cap = float(getattr(cfg, "fragdelta_flop_cap", 2e9))
    frag_hor = int(getattr(cfg, "fragdelta_horizon", 0) or 0)
    frag_flops, frag_alive = 0.0, True
    # hull-nestle 회수(issue/06): FLOP 캡은 fragdelta와 동형(결정론적 셧오프).
    nes_k = int(getattr(cfg, "dispatch_nestle_k", 0) or 0)
    nes_cap = int(getattr(cfg, "dispatch_nestle_cap", 12) or 0)
    nes_flop_cap = float(getattr(cfg, "dispatch_nestle_flop_cap", 2e9))
    nes_flops, nes_alive = 0.0, True
    nes_space = None
    if nes_k > 0:
        from .nestle import ExactSpace
        nes_space = ExactSpace(pre, fast=bool(
            getattr(cfg, "dispatch_nestle_fast", True)))
    pbar = (sum(P) / n) if n else 1.0
    amin = [min(pre.area[i]) for i in range(n)]
    abar = (sum(amin) / n) if n else 1.0
    # 동적 bay: 부하 균형용 bay별 점유면적/용량 추적(least-util bay로 라우팅).
    bay_area = bay_occ = None
    if dyn_bay:
        _bd = prob_info["bays"]
        bay_area = [max(1.0, _bd[j]["width"] * _bd[j]["height"])
                    for j in range(pre.n_bays)]
        bay_occ = [0.0] * pre.n_bays
    kappa = max(1e-9, float(cfg.atc_kappa))
    alpha = float(cfg.atc_alpha)
    weights = prob_info.get("weights", {})
    w1 = float(weights.get("w1", 1.0))
    w3 = float(weights.get("w3", 1.0))
    S = [b.get("bay_preferences", []) for b in blocks]
    Smax = getattr(pre, "Smax", [max(s) if s else 0.0 for s in S])

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

    def _gate_with(i, j, o, pos, xt, placed_j, coords_map, orient_map, exit_map):
        if not _collision_free(i, o, pos, placed_j, coords_map, orient_map, pre):
            return False
        for k in placed_j:
            if exit_map[k] <= xt and crane_blocks_resident(
                    i, o, pos, k, coords_map, orient_map, pre):
                return False
        stayers = [k for k in placed_j if exit_map[k] >= xt]
        if stayers and crane_obstructed(i, o, pos, stayers, coords_map, orient_map, pre):
            return False
        return True

    def _exact_gate(i, j, o, pos, xt):
        # scan이 증명 못 하는 시간축 두 가지만 정확 검사:
        #  (a) 내 체류 중 exit하는 상주의 반출을 내가 막는가 (역방향 차단)
        #  (b) 내 exit(xt) 시점 잔류 상주가 내 반출을 막는가
        # 경계(exit == xt)는 양쪽 모두에 포함 = 이중 보수 (id tie-break 미러 회피).
        return _gate_with(i, j, o, pos, xt, placed[j], coords, orient, exit_)

    def _try_nestle(i, j, t, xt):
        # 마스크-비가시 합법 앵커 회수(N2). 후보 = 0<count<=K, count 오름차순
        # (동률은 행우선 = bottom-left) 상위 cap개. 공간은 exact polygon, 크레인은
        # 기존 _exact_gate -- 커밋 경로는 기존과 동일(hull 마스크 스탬프 유지).
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
        # 마감 후엔 스캔 진입 자체를 막는다(마감 전엔 항상 통과 = 비트동일).
        if deadline is not None and time.perf_counter() >= deadline:
            return False
        xt = t + P[i]
        cap = (cfg.dispatch_cand_cap_hi if len(queue[j]) >= cfg.dispatch_queue_hi
               else cfg.dispatch_cand_cap)
        # ΔF 페널티용 미래 표적(자기 자신은 제외 -- 제 앵커를 스스로 피하지 않게).
        futs = None
        if futures:
            futs = [f for f in futures if f[0] != i] or None
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
            for (r, c) in raster.order_cells(j, i, o, allow, cap, futs, frag_w):
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
        # -- N2 hull-nestle 회수: mask-feasible 패스 전멸 시에만 (K=0 = 미진입) --
        if nes_k > 0 and nes_alive and _try_nestle(i, j, t, xt):
            PROBE.on_attempt(t, j, i, rank, earlier, "admitted",
                             feas_anchors, cells_tried, gate_rej)
            return True
        PROBE.on_attempt(t, j, i, rank, earlier,
                         "gate_fail" if any_space else "no_space",
                         feas_anchors, cells_tried, gate_rej)
        return False

    def _pref_gap(i, j):
        pref = S[i][j] if j < len(S[i]) else 0.0
        return Smax[i] - pref

    def _beam_bay_candidates(i, node, t):
        cands = [node["bay"][i]]
        if dyn_bay and t + P[i] > D[i]:
            alts = sorted((b for b in elig_bays[i] if b != node["bay"][i]),
                          key=lambda b: node["bay_occ"][b] / bay_area[b])
            cands.extend(alts[:2])
        out = []
        for b in cands:
            if b not in out:
                out.append(b)
        return out

    def _beam_candidate_actions(i, j, t, node, rank=0):
        xt = t + P[i]
        cap = max(1, int(getattr(cfg, "beam_top_anchors", 4) or 4))
        actions = []
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
            for anchor_rank, (r, c) in enumerate(raster.order_cells(j, i, o, allow, cap)):
                pos = (int(c) - mx0, int(r) - my0)
                if not _gate_with(i, j, o, pos, xt, node["placed"][j],
                                  node["coords"], node["orient"], node["exit"]):
                    continue
                util = node["bay_occ"][j] / bay_area[j] if dyn_bay else 0.0
                tard = max(0, xt - D[i])
                cost = (w1 * tard
                        + w3 * _pref_gap(i, j)
                        + float(getattr(cfg, "beam_congestion_penalty", 200.0)) * util * util
                        + 0.001 * (rank + anchor_rank))
                actions.append({
                    "block": i, "bay": j, "orient": o, "pos": pos,
                    "entry": t, "exit": xt, "cost": cost,
                })
        actions.sort(key=lambda a: (a["cost"], a["block"], a["bay"], a["orient"], a["pos"]))
        return actions[:cap]

    def _beam_apply(node, action):
        i, j = action["block"], action["bay"]
        nxt = {
            "placed": [x.copy() for x in node["placed"]],
            "coords": dict(node["coords"]),
            "orient": dict(node["orient"]),
            "entry": list(node["entry"]),
            "exit": list(node["exit"]),
            "bay": list(node["bay"]),
            "bay_occ": list(node["bay_occ"]),
            "remaining": [x for x in node["remaining"] if x != i],
            "score": node["score"] + action["cost"],
            "first": node["first"] if node["first"] is not None else action,
        }
        nxt["coords"][i] = action["pos"]
        nxt["orient"][i] = action["orient"]
        nxt["entry"][i] = action["entry"]
        nxt["exit"][i] = action["exit"]
        nxt["bay"][i] = j
        nxt["placed"][j].append(i)
        if dyn_bay:
            nxt["bay_occ"][j] += amin[i]
        return nxt

    def _beam_eval(node, t):
        top = sorted(node["remaining"], key=lambda b: (-_prio(b, t), b))[:8]
        lb_tard = sum(max(0, t + P[i] - D[i]) for i in top)
        return node["score"] + 0.25 * w1 * lb_tard

    def _critical_event(j, t):
        if not bool(getattr(cfg, "dispatch_beam", False)):
            return False
        trigger_q = int(getattr(cfg, "beam_trigger_queue", 8) or 8)
        if len(queue[j]) >= trigger_q:
            return True
        top_n = max(1, int(getattr(cfg, "beam_top_blocks", 4) or 4))
        top = sorted(queue[j], key=lambda b: (-_prio(b, t), b))[:top_n]
        if any(t + P[i] > D[i] for i in top):
            return True
        if dyn_bay and len(queue[j]) >= max(2, trigger_q // 2) and any(len(elig_bays[i]) > 1 for i in top):
            return True
        return False

    def _beam_first_action(j, t):
        top_blocks = max(1, int(getattr(cfg, "beam_top_blocks", 4) or 4))
        depth = max(1, int(getattr(cfg, "beam_depth", 3) or 3))
        width = max(1, int(getattr(cfg, "beam_width", 8) or 8))
        max_exp = max(1, int(getattr(cfg, "beam_max_expansions", 256) or 256))
        root = {
            "placed": [x.copy() for x in placed],
            "coords": dict(coords),
            "orient": dict(orient),
            "entry": list(entry),
            "exit": list(exit_),
            "bay": list(bay),
            "bay_occ": list(bay_occ) if dyn_bay else [0.0] * pre.n_bays,
            "remaining": list(queue[j]),
            "score": 0.0,
            "first": None,
        }
        beam = [root]
        expansions = 0
        for _ in range(depth):
            nxt = []
            for node in beam:
                choices = sorted(node["remaining"], key=lambda b: (-_prio(b, t), b))[:top_blocks]
                for rank, i in enumerate(choices):
                    for bj in _beam_bay_candidates(i, node, t):
                        for action in _beam_candidate_actions(i, bj, t, node, rank):
                            nxt.append(_beam_apply(node, action))
                            expansions += 1
                            if expansions >= max_exp:
                                break
                        if expansions >= max_exp:
                            break
                    if expansions >= max_exp:
                        break
                if expansions >= max_exp:
                    break
            if not nxt:
                break
            nxt.sort(key=lambda nd: _beam_eval(nd, t))
            beam = nxt[:width]
            if expansions >= max_exp:
                break
        best = min(beam, key=lambda nd: _beam_eval(nd, t))
        return best["first"]

    def _commit_action(action, from_j):
        i, j = action["block"], action["bay"]
        if not _gate_with(i, j, action["orient"], action["pos"], action["exit"],
                          placed[j], coords, orient, exit_):
            return False
        coords[i] = action["pos"]
        orient[i] = action["orient"]
        entry[i] = action["entry"]
        exit_[i] = action["exit"]
        raster.add(j, i, action["orient"], action["pos"])
        placed[j].append(i)
        bay[i] = j
        queue[from_j].remove(i)
        if dyn_bay:
            bay_occ[j] += amin[i]
        return True

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
            # ΔF pre-pass: M*(큐 대형 Q개) feasible 지도 SAT를 패스당 1회 구축.
            # 패스 중 admit로 낡아도 그대로 씀(랭킹 휴리스틱).
            futures = None
            if frag_w > 0.0 and frag_alive and frag_q > 0 \
                    and len(queue[j]) >= int(getattr(cfg, "fragdelta_queue_hi", 6)):
                pool = list(queue[j])
                if frag_hor > 0:
                    # 완전 예지: t+H 내 도착 예정(미방출)인 이 bay 배정 블록도 표적.
                    k = ri
                    while k < n and R[order[k]] <= t + frag_hor:
                        if bay[order[k]] == j:
                            pool.append(order[k])
                        k += 1
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
                        # 신규(또는 낡은) 스캔 = 실비용. 전체 재계산 상한으로 계정
                        # (증분이면 과대계상 = 보수적 셧오프).
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
            _earlier = 0     # 진단: 같은 tick·bay 에서 앞서 admit 된 수 (캐스케이드 깊이)
            _fails = 0       # 마지막 admit 이후 연속 실패 수 (조기중단 카운터)
            if _critical_event(j, t):
                action = _beam_first_action(j, t)
                if action is not None and _commit_action(action, j):
                    _earlier += 1
                    x = int(exit_[action["block"]])
                    if x not in in_ev:
                        heapq.heappush(ev, x)
                        in_ev.add(x)
            for _rank, i in enumerate(sorted(queue[j], key=lambda b: (-_prio(b, t), b))):
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
                    # 조건부: 지금 넣어도 지각(t+P>D)인 급한 블록만 다른 bay로 admit-now.
                    # 여유 블록은 제 bay 대기(비혼잡 오라우팅 회귀 방지).
                    if dyn_bay and t + P[i] > D[i]:
                        # 다른 eligible bay 중 가장 여유있는(least-util) 순으로 시도.
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
                    # 마지막 admit 이후 F회 연속 실패 -> 남은 큐는 다음 이벤트로 이월.
                    if fail_stop and _fails >= fail_stop:
                        break

    # -- 잔여(마감 초과 포함) -> 빈 창 강제 배치: 출력은 항상 완전한 배정 --------
    # per-bay 스케줄 스냅숏을 1회 구축 후 append. empty_bay_entry 고정점은 구간
    # '집합'에만 의존(순서 무관)하므로 매 호출 range(n) 재수집과 비트동일.
    # coords 기준(placed[j] 아님): exit한 블록도 포함해야 기존 필터와 일치.
    _sched = [[] for _ in range(pre.n_bays)]
    _tail = [0] * pre.n_bays
    for k in coords:
        _sched[bay[k]].append((entry[k], exit_[k]))
        if exit_[k] > _tail[bay[k]]:
            _tail[bay[k]] = exit_[k]

    def _force(i):
        j = bay[i]
        if deadline is not None and time.perf_counter() >= deadline:
            # 마감 후: 빈-창 탐색(bay당 O(k²)) 대신 tail-pointer(O(1)). bay tail
            # 이후라 빈 창=feasible; 절단 해는 min-wins서 버려진다.
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
