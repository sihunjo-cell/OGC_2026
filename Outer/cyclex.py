"""Outer.cyclex -- cycle-exchange proposals over bay assignments.

This is a structural move for the bay-assignment layer.  It proposes simultaneous
bay cycles such as A->bay(B), B->bay(C), C->bay(A).  The proxy is used only to
produce a short candidate list; ALNS evaluates those candidates with Phase2.
"""

from __future__ import annotations

import itertools
import time

from Phase1.common import eligible, footprint_area
from .destroy import _normalizers, r_related
from .objective import loads_from_bay, z2_raw


def _areas(pre):
    a = getattr(pre, "_cyclex_areas", None)
    if a is None:
        a = [footprint_area(pre, i) for i in range(pre.n_blocks)]
        object.__setattr__(pre, "_cyclex_areas", a)
    return a


def _pref_loss(i, j, prefs, smax):
    return smax[i] - (prefs[i][j] if j < len(prefs[i]) else 0)


def _time_overlap(entry, exit_, i, j):
    return entry[i] < exit_[j] and entry[j] < exit_[i]


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


def _candidate_nodes(s, prob_info, pre, cfg, ranks, prefs, due, areas):
    """Build a bounded candidate set: tardy seeds + related occupants + pref-loss."""
    n = len(ranks)
    max_nodes = max(2, int(getattr(cfg, "cyclex_nodes", 24) or 24))
    pref_nodes = max(0, int(getattr(cfg, "cyclex_pref_nodes", max_nodes // 4) or 0))
    neighbor_k = max(0, int(getattr(cfg, "cyclex_neighbor_k", 3) or 0))

    by_cost = [i for *_rest, i in sorted(ranks, reverse=True)]
    tard = [(max(0, s.exit_[i] - due[i]), ranks[i][0], i) for i in range(n)]
    by_tard = [i for *_rest, i in sorted(tard, reverse=True)]
    pref = [(_pref_loss(i, s.bay[i], prefs, pre.Smax), ranks[i][0], i) for i in range(n)]
    by_pref = [i for *_rest, i in sorted(pref, reverse=True)]

    nodes = []
    seen = set()

    def add(i):
        if i not in seen and len(nodes) < max_nodes:
            seen.add(i)
            nodes.append(i)
            return True
        return False

    seed_cap = max(2, min(max_nodes, (max_nodes + 1) // 2))
    seeds = []
    for i in by_tard:
        if len(seeds) >= seed_cap:
            break
        if tard[i][0] > 0 or ranks[i][0] > 0:
            seeds.append(i)
            add(i)
    for i in by_cost:
        if len(seeds) >= seed_cap:
            break
        if i not in seeds:
            seeds.append(i)
            add(i)

    for i in by_pref[:pref_nodes]:
        add(i)

    try:
        norm = _normalizers(prob_info, pre)
    except Exception:
        norm = None
    for seed in seeds:
        if len(nodes) >= max_nodes:
            break
        overlap = [j for j in range(n)
                   if j != seed and s.bay[j] != s.bay[seed]
                   and _time_overlap(s.entry, s.exit_, seed, j)]
        overlap.sort(key=lambda j: (abs(s.entry[seed] - s.entry[j]),
                                    abs(areas[seed] - areas[j])))
        for j in overlap[:neighbor_k]:
            add(j)
        if norm is not None and len(nodes) < max_nodes:
            rel = [j for j in range(n) if j != seed and j not in seen]
            rel.sort(key=lambda j: r_related(seed, j, s, cfg, pre, norm))
            for j in rel[:neighbor_k]:
                add(j)

    for i in by_cost:
        if len(nodes) >= max_nodes:
            break
        add(i)
    return nodes, len(seeds)


def propose_cyclex(s, prob_info: dict, pre, cfg, rng=None, deadline=None):
    """Return ``(candidates, info)``.

    ``candidates`` is sorted by proxy delta and contains dictionaries with:
    ``bay``, ``proxy_delta``, and ``move_size``.  The caller should run exact
    ``realize`` on each candidate and accept only actual objective improvement.
    """
    info = {"candidate": 0, "checked": 0, "nodes": 0, "seeds": 0,
            "move_size": 0, "proxy_delta": None, "n_candidates": 0}
    if deadline is not None and time.perf_counter() >= deadline:
        return [], info

    blocks = prob_info["blocks"]
    n = len(blocks)
    m = pre.n_bays
    if n < 2 or m < 2:
        return [], info

    max_cycles = max(1, int(getattr(cfg, "cyclex_max_cycles", 20000) or 20000))
    min_gain = float(getattr(cfg, "cyclex_min_proxy_gain", 0.0) or 0.0)
    realize_k = max(1, int(getattr(cfg, "cyclex_realize_k", 4) or 4))

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

    ranks = []
    for i in range(n):
        tard = max(0, s.exit_[i] - due[i])
        pref = _pref_loss(i, s.bay[i], prefs, pre.Smax)
        ranks.append((w1 * tard + w3 * pref, tard, areas[i], i))
    nodes, n_seeds = _candidate_nodes(s, prob_info, pre, cfg, ranks, prefs, due, areas)
    info["nodes"] = len(nodes)
    info["seeds"] = n_seeds
    if len(nodes) < 2:
        return [], info

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

    top = []
    seen_maps = set()
    checked = 0

    def add_candidate(mapping, delta):
        if delta is None or delta >= -min_gain:
            return
        key = tuple(sorted(mapping.items()))
        if key in seen_maps:
            return
        seen_maps.add(key)
        bay = list(s.bay)
        for i, j in mapping.items():
            bay[i] = j
        top.append({"bay": bay, "proxy_delta": delta, "move_size": len(mapping)})
        top.sort(key=lambda c: c["proxy_delta"])
        if len(top) > realize_k:
            top.pop()

    for a, b in itertools.combinations(nodes, 2):
        if deadline is not None and time.perf_counter() >= deadline:
            break
        if s.bay[a] == s.bay[b]:
            continue
        mp = {a: s.bay[b], b: s.bay[a]}
        add_candidate(mp, eval_mapping(mp))
        checked += 1
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
                add_candidate(mp, eval_mapping(mp))
                checked += 1
                if checked >= max_cycles:
                    break
            if checked >= max_cycles:
                break

    info["checked"] = checked
    info["n_candidates"] = len(top)
    if top:
        info.update(candidate=1, move_size=top[0]["move_size"],
                    proxy_delta=top[0]["proxy_delta"])
    return top, info