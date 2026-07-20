"""Outer.inbay -- real-scored in-bay dispatch order perturbations.

The bay assignment vector is unchanged.  Candidates only provide a
``dispatch_order_hint`` mapping block_id -> priority rank, which Phase2.dispatch
uses to admit hinted blocks earlier within their release ticks.
"""

from __future__ import annotations

from Phase1.common import footprint_area


def _areas(pre):
    a = getattr(pre, "_inbay_areas", None)
    if a is None:
        a = [footprint_area(pre, i) for i in range(pre.n_blocks)]
        object.__setattr__(pre, "_inbay_areas", a)
    return a


def propose_inbay(s, prob_info: dict, pre, cfg):
    """Return a list of order-hint candidates for exact realization."""
    blocks = prob_info["blocks"]
    n = len(blocks)
    if n <= 1:
        return []

    max_cands = max(1, int(getattr(cfg, "inbay_realize_k", 4) or 4))
    max_bays = max(1, int(getattr(cfg, "inbay_bays", 2) or 2))
    top_per_bay = max(2, int(getattr(cfg, "inbay_top", 10) or 10))

    weights = prob_info.get("weights", {})
    w1 = float(weights.get("w1", 1.0))
    w3 = float(weights.get("w3", 1.0))
    due = [b["due_date"] for b in blocks]
    proc = [b["processing_time"] for b in blocks]
    prefs = [b["bay_preferences"] for b in blocks]
    areas = _areas(pre)

    bay_blocks = [[] for _ in range(pre.n_bays)]
    for i, j in enumerate(s.bay):
        if 0 <= j < pre.n_bays:
            bay_blocks[j].append(i)

    def pref_loss(i):
        j = s.bay[i]
        return pre.Smax[i] - (prefs[i][j] if j < len(prefs[i]) else 0)

    def tard(i):
        return max(0, s.exit_[i] - due[i])

    def slack(i):
        return due[i] - s.entry[i] - proc[i]

    bay_rank = []
    for j, ids in enumerate(bay_blocks):
        if len(ids) < 2:
            continue
        z = sum(w1 * tard(i) + w3 * pref_loss(i) for i in ids)
        z += 0.01 * sum(areas[i] for i in ids)
        bay_rank.append((z, len(ids), j))
    bay_ids = [j for *_rest, j in sorted(bay_rank, reverse=True)[:max_bays]]

    cands = []
    seen = set()

    def add(j, name, ordered):
        ids = []
        used = set()
        for i in ordered:
            if i in used:
                continue
            if s.bay[i] != j:
                continue
            ids.append(i)
            used.add(i)
            if len(ids) >= top_per_bay:
                break
        if len(ids) < 2:
            return
        key = tuple(ids)
        if key in seen:
            return
        seen.add(key)
        hint = {int(i): r for r, i in enumerate(ids)}
        cands.append({"hint": hint, "bay": j, "mode": name, "size": len(ids)})

    for j in bay_ids:
        ids = list(bay_blocks[j])
        add(j, "tardy_first", sorted(ids, key=lambda i: (-tard(i), slack(i), s.entry[i], i)))
        add(j, "least_slack", sorted(ids, key=lambda i: (slack(i), -tard(i), s.entry[i], i)))
        add(j, "edd", sorted(ids, key=lambda i: (due[i], -tard(i), i)))
        add(j, "large_first", sorted(ids, key=lambda i: (-areas[i], slack(i), i)))
        add(j, "late_entry_first", sorted(ids, key=lambda i: (-s.entry[i], slack(i), i)))
        if len(cands) >= max_cands:
            break

    return cands[:max_cands]