"""Serial SGS Phase 2 dispatcher.

This worker is activity-incremental: choose an unscheduled block, find the
earliest feasible insertion in the partial schedule, commit it, and repeat.
It is intended as a portfolio-diversity worker, not a replacement for the
event-driven dispatcher.
"""

from __future__ import annotations

import math
import time

import numpy as np

from . import repair as rp
from .collision import _collision_free
from .crane import crane_blocks_resident, crane_obstructed
from .raster import Raster


def dispatch_construct_serial(prob_info: dict, p1_out, pre, cfg, deadline=None):
    blocks = prob_info["blocks"]
    bays = prob_info["bays"]
    n = len(blocks)
    P = [b["processing_time"] for b in blocks]
    D = [b["due_date"] for b in blocks]
    R = [b["release_time"] for b in blocks]
    S = [b.get("bay_preferences", []) for b in blocks]
    Smax = getattr(pre, "Smax", [max(s) if s else 0.0 for s in S])
    weights = prob_info.get("weights", {})
    w1 = float(weights.get("w1", 1.0))
    w3 = float(weights.get("w3", 1.0))

    bay = list(p1_out.bay)
    entry = list(p1_out.entry)
    exit_ = list(p1_out.exit_)
    coords: dict = {}
    orient: dict = {}
    scheduled_by_bay = [[] for _ in range(pre.n_bays)]
    scheduled_area = [0.0] * pre.n_bays
    bay_area = [max(1.0, b["width"] * b["height"]) for b in bays]
    amin = [min(pre.area[i]) for i in range(n)]
    abar = (sum(amin) / n) if n else 1.0
    pbar = (sum(P) / n) if n else 1.0
    forced_cons: set = set()

    kappa = max(1e-9, float(getattr(cfg, "atc_kappa", 2.0)))
    area_alpha = float(getattr(cfg, "serial_area_alpha", 0.7))
    long_alpha = float(getattr(cfg, "serial_long_alpha", 0.5))
    top_blocks = max(1, int(getattr(cfg, "serial_top_blocks", 6) or 6))
    top_bays = max(1, int(getattr(cfg, "serial_top_bays", 3) or 3))
    anchor_cap = max(1, int(getattr(cfg, "serial_anchor_cap", 8) or 8))
    time_cap = max(1, int(getattr(cfg, "serial_time_cap", 32) or 32))
    rank_penalty = float(getattr(cfg, "serial_rank_penalty", 1000.0))
    dyn_bay = bool(getattr(cfg, "serial_dynamic_bay", True))

    eligible = []
    for i in range(n):
        cands = []
        for j in range(pre.n_bays):
            for o in range(len(pre.poly[i])):
                (xl, xh), (yl, yh) = pre.IFP[i][o][j]
                if xl <= xh and yl <= yh:
                    cands.append(j)
                    break
        eligible.append(cands)

    def _past():
        return deadline is not None and time.perf_counter() >= deadline

    def _pref_gap(i, j):
        pref = S[i][j] if j < len(S[i]) else 0.0
        return Smax[i] - pref

    def _priority(i):
        slack0 = D[i] - P[i] - R[i]
        atc = (1.0 / max(1, P[i])) * math.exp(-max(0.0, slack0) / (kappa * pbar))
        area = (amin[i] / abar) ** area_alpha if abar > 0 else 1.0
        longp = (P[i] / pbar) ** long_alpha if pbar > 0 else 1.0
        scarcity = pre.n_bays / max(1, len(eligible[i]))
        return atc * area * longp * scarcity

    def _bay_candidates(i):
        home = bay[i]
        cands = [home]
        if dyn_bay:
            alts = sorted(
                (j for j in eligible[i] if j != home),
                key=lambda j: (
                    scheduled_area[j] / bay_area[j],
                    _pref_gap(i, j),
                    j,
                ),
            )
            cands.extend(alts[:max(0, top_bays - 1)])
        out = []
        for j in cands:
            if j in eligible[i] and j not in out:
                out.append(j)
        return out or eligible[i][:top_bays] or [home]

    def _candidate_times(i, j):
        times = {int(R[i])}
        for k in scheduled_by_bay[j]:
            if entry[k] >= R[i]:
                times.add(int(entry[k]))
            if exit_[k] >= R[i]:
                times.add(int(exit_[k]))
        return sorted(times)[:time_cap]

    def _gate(i, j, o, pos, t, xt, overlap_res, entry_res, exit_res):
        if not _collision_free(i, o, pos, overlap_res, coords, orient, pre):
            return False
        if entry_res and crane_obstructed(i, o, pos, entry_res, coords, orient, pre):
            return False
        for k in overlap_res:
            if t <= entry[k] < xt and crane_blocks_resident(i, o, pos, k, coords, orient, pre):
                return False
            if t < exit_[k] <= xt and crane_blocks_resident(i, o, pos, k, coords, orient, pre):
                return False
        if exit_res and crane_obstructed(i, o, pos, exit_res, coords, orient, pre):
            return False
        return True

    def _scratch_raster(j, residents):
        raster = Raster(
            prob_info,
            pre,
            incremental=False,
            mask_share=bool(getattr(cfg, "mask_cache_share", False)),
        )
        for k in residents:
            raster.add(j, k, orient[k], coords[k])
        return raster

    def _actions_for(i, j, t):
        xt = t + P[i]
        residents = scheduled_by_bay[j]
        overlap_res = [k for k in residents if entry[k] < xt and t < exit_[k]]
        entry_res = [k for k in residents if entry[k] <= t < exit_[k]]
        exit_res = [k for k in residents if entry[k] < xt <= exit_[k]]
        raster = _scratch_raster(j, overlap_res)
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
            for anchor_rank, (r, c) in enumerate(raster.order_cells(j, i, o, allow, anchor_cap)):
                pos = (int(c) - mx0, int(r) - my0)
                if not _gate(i, j, o, pos, t, xt, overlap_res, entry_res, exit_res):
                    continue
                actions.append((anchor_rank, pos, o, t, xt))
        return actions

    def _earliest_insert(i):
        best = None
        for j in _bay_candidates(i):
            for time_rank, t in enumerate(_candidate_times(i, j)):
                actions = _actions_for(i, j, t)
                if not actions:
                    continue
                tard = max(0, t + P[i] - D[i])
                util = scheduled_area[j] / bay_area[j]
                for anchor_rank, pos, o, e, x in actions:
                    cost = (
                        w1 * tard
                        + w3 * _pref_gap(i, j)
                        + 250.0 * util * util
                        + 0.01 * max(0, e - R[i])
                        + 0.001 * (time_rank + anchor_rank)
                    )
                    cand = (cost, e, x, j, o, pos)
                    if best is None or cand < best:
                        best = cand
                break
        return best

    def _force(i):
        for j in _bay_candidates(i):
            try:
                sched = [(entry[k], exit_[k]) for k in scheduled_by_bay[j]]
                pos, o, e, x = rp.force_place(i, j, sched, pre, R, P)
                return (float("inf"), e, x, j, o, pos)
            except Exception:
                continue
        j = bay[i]
        sched = [(entry[k], exit_[k]) for k in scheduled_by_bay[j]]
        pos, o, e, x = rp.force_place(i, j, sched, pre, R, P)
        return (float("inf"), e, x, j, o, pos)

    unscheduled = set(range(n))
    while unscheduled:
        if _past():
            break
        ranked = sorted(unscheduled, key=lambda i: (-_priority(i), D[i], R[i], i))
        best = None
        for rank, i in enumerate(ranked[:top_blocks]):
            action = _earliest_insert(i)
            if action is None:
                continue
            cost, e, x, j, o, pos = action
            score = cost + rank_penalty * rank
            cand = (score, i, e, x, j, o, pos)
            if best is None or cand < best:
                best = cand
        if best is None:
            i = ranked[0]
            _, e, x, j, o, pos = _force(i)
            forced_cons.add(i)
        else:
            _, i, e, x, j, o, pos = best
        bay[i] = j
        coords[i] = pos
        orient[i] = o
        entry[i] = e
        exit_[i] = x
        scheduled_by_bay[j].append(i)
        scheduled_area[j] += amin[i]
        unscheduled.remove(i)

    for i in list(unscheduled):
        _, e, x, j, o, pos = _force(i)
        forced_cons.add(i)
        bay[i] = j
        coords[i] = pos
        orient[i] = o
        entry[i] = e
        exit_[i] = x
        scheduled_by_bay[j].append(i)
        scheduled_area[j] += amin[i]

    return coords, orient, entry, exit_, forced_cons, bay
