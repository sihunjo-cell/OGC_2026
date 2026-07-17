"""Outer.cyclex -- cycle-exchange proposals over bay assignments.

This is a structural move for the bay-assignment layer. It proposes simultaneous
bay cycles such as A->bay(B), B->bay(C), C->bay(A); the caller must still realize
the proposed bay vector with Phase2 before accepting it.
"""

from __future__ import annotations

import itertools
import time

from Phase1.common import eligible, footprint_area
from .objective import loads_from_bay, z2_raw


def _areas(pre):
    a = getattr(pre, "_cyclex_areas", None)
    if a is None:
        a = [footprint_area(pre, i) for i in range(pre.n_blocks)]
        object.__setattr__(pre, "_cyclex_areas", a)
    return a


def _pref_loss(i, j, prefs, smax):
    return smax[i] - (prefs[i][j] if j < len(prefs[i]) else 0)


def _crowd_score(i, j, prof, bays, est, proc, areas, cw, eta, w1):
    if cw <= 0.0:
        return 0.0
    wh = max(1.0, bays[j]["width"] * bays[j]["height"])
    ei, xi, ai = est[i], est[i] + proc[i], areas[i]
    times = [ei] + [a for (bid, a, e, _ar) in prof[j]
                    if bid != i and ei <= a < xi]
    peak = 0.0
    for t in times:
        s = ai
        for bid, a, e, ar in prof[j]:
            if bid != i and a <= t < e:
                s += ar
        if s > peak:
            peak = s
    over = peak - eta * wh
    return (cw * w1 * over / wh) if over > 0.0 else 0.0


def propose_cyclex(s, prob_info: dict, pre, cfg, rng=None, deadline=None):
    """Return a proposed bay vector or None.

    Candidate cycles are ranked by a cheap assignment proxy:
    exact Z2 load delta + local preference/crowd deltas for moved blocks. The
    proxy only chooses one candidate; the caller decides by exact realize().
    """
    if deadline is not None and time.perf_counter() >= deadline:
        return None

    blocks = prob_info["blocks"]
    n = len(blocks)
    m = pre.n_bays
    if n < 2 or m < 2:
        return None

    max_nodes = max(2, int(getattr(cfg, "cyclex_nodes", 24) or 24))
    max_cycles = max(1, int(getattr(cfg, "cyclex_max_cycles", 20000) or 20000))
    min_gain = float(getattr(cfg, "cyclex_min_proxy_gain", 0.0) or 0.0)

    weights = prob_info.get("weights", {})
    w1 = weights.get("w1", 1.0)
    w2 = weights.get("w2", 1.0)
    w3 = weights.get("w3", 1.0)
    prefs = [b["bay_preferences"] for b in blocks]
    proc = [b["processing_time"] for b in blocks]
    loads = [b["workload"] for b in blocks]
    est = pre.EST
    due = [b["due_date"] for b in blocks]
    areas = _areas(pre)

    prof = [[] for _ in range(m)]
    for i, j in enumerate(s.bay):
        prof[j].append((i, est[i], est[i] + proc[i], areas[i]))

    cw = float(getattr(cfg, "crowd_weight", 0.0) or 0.0)
    eta = float(getattr(cfg, "crowd_eta", 0.85) or 0.85)

    def local_score(i, j):
        return (w3 * _pref_loss(i, j, prefs, pre.Smax)
                + _crowd_score(i, j, prof, prob_info["bays"], est, proc,
                               areas, cw, eta, w1))

    cur_loads = loads_from_bay(s.bay, loads, m)
    cur_z2 = z2_raw(cur_loads, pre.u)
    local_cur = {}

    rank = []
    for i in range(n):
        tard = max(0, s.exit_[i] - due[i])
        pref = _pref_loss(i, s.bay[i], prefs, pre.Smax)
        rank.append((w1 * tard + w3 * pref, tard, areas[i], i))
    nodes = [i for *_rest, i in sorted(rank, reverse=True)[:max_nodes]]
    if len(nodes) < 2:
        return None

    def can_move(i, j):
        return j != s.bay[i] and eligible(pre, i, j)

    def eval_mapping(mapping):
        new_loads = list(cur_loads)
        delta_local = 0.0
        for i, j_new in mapping.items():
            j_old = s.bay[i]
            if not can_move(i, j_new):
                return None
            new_loads[j_old] -= loads[i]
            new_loads[j_new] += loads[i]
            if i not in local_cur:
                local_cur[i] = local_score(i, j_old)
            delta_local += local_score(i, j_new) - local_cur[i]
        return w2 * (z2_raw(new_loads, pre.u) - cur_z2) + delta_local

    best_delta = -min_gain
    best_map = None
    checked = 0

    for a, b in itertools.combinations(nodes, 2):
        if deadline is not None and time.perf_counter() >= deadline:
            break
        if s.bay[a] == s.bay[b]:
            continue
        d = eval_mapping({a: s.bay[b], b: s.bay[a]})
        checked += 1
        if d is not None and d < best_delta:
            best_delta = d
            best_map = {a: s.bay[b], b: s.bay[a]}
        if checked >= max_cycles:
            break

    if checked < max_cycles:
        for a, b, c in itertools.permutations(nodes, 3):
            if a >= b or b >= c:
                continue
            if deadline is not None and time.perf_counter() >= deadline:
                break
            triples = (
                {a: s.bay[b], b: s.bay[c], c: s.bay[a]},
                {a: s.bay[c], c: s.bay[b], b: s.bay[a]},
            )
            for mp in triples:
                if len({s.bay[x] for x in mp}) < 2:
                    continue
                d = eval_mapping(mp)
                checked += 1
                if d is not None and d < best_delta:
                    best_delta = d
                    best_map = dict(mp)
                if checked >= max_cycles:
                    break
            if checked >= max_cycles:
                break

    if not best_map:
        return None
    bay = list(s.bay)
    for i, j in best_map.items():
        bay[i] = j
    return bay