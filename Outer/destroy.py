"""destroy 연산자 3종 (random / worst / related-Shaw) -> (partial bay, 제거 집합)."""

from __future__ import annotations

from Phase1.common import footprint_area
from Phase2 import geometry_query as gq
from .objective import block_cost


def _normalizers(prob_info, pre):
    n = len(prob_info["blocks"])
    entries = pre.EST
    span = max(1.0, max(entries) - min(entries))
    areas = [footprint_area(pre, i) for i in range(n)]
    max_area = max(1.0, max(areas))
    max_diag = max(gq.bay_diagonal(prob_info, j) for j in range(pre.n_bays))
    return span, areas, max_area, max_diag


def _center(s, pre, i):
    return gq.centroid_of_bbox(gq.world_bbox(pre, i, s.orient[i], s.coords[i]))


def r_related(i, j, s, cfg, pre, norm):
    span, areas, max_area, max_diag = norm
    same_bay = 1.0 if s.bay[i] == s.bay[j] else 0.0
    dt = abs(s.entry[i] - s.entry[j]) / span
    ci, cj = _center(s, pre, i), _center(s, pre, j)
    prox = ((ci[0] - cj[0]) ** 2 + (ci[1] - cj[1]) ** 2) ** 0.5 / max_diag
    shape = abs(areas[i] - areas[j]) / max_area
    return cfg.phi * same_bay + cfg.chi * dt + cfg.psi * prox + cfg.omega * shape


def _pick_biased(sorted_ids, y, p):
    """y^p 편향 선택 (p 클수록 앞쪽)."""
    idx = int((y ** p) * len(sorted_ids))
    if idx >= len(sorted_ids):
        idx = len(sorted_ids) - 1
    return sorted_ids[idx]


def _destroy_random(s, q, cfg, prob_info, pre, rng):
    n = len(s.bay)
    return set(rng.sample(range(n), q))


def _destroy_worst(s, q, cfg, prob_info, pre, rng):
    n = len(s.bay)
    w = prob_info.get("weights", {})
    w1, w3 = w.get("w1", 1.0), w.get("w3", 1.0)
    D = [prob_info["blocks"][i]["due_date"] for i in range(n)]
    Smax = pre.Smax
    S = [b["bay_preferences"] for b in prob_info["blocks"]]
    ranked = sorted(range(n), key=lambda i: block_cost(i, s, w1, w3, D, Smax, S), reverse=True)
    chosen = set()
    while len(chosen) < q:
        i = _pick_biased(ranked, rng.random(), cfg.p_worst)
        chosen.add(i)
    return chosen


def _destroy_related(s, q, cfg, prob_info, pre, rng):
    n = len(s.bay)
    norm = _normalizers(prob_info, pre)
    seed = rng.randrange(n)
    D = {seed}
    while len(D) < q:
        r = rng.choice(tuple(D))
        pool = sorted((i for i in range(n) if i not in D),
                      key=lambda i: r_related(r, i, s, cfg, pre, norm))
        if not pool:
            break
        D.add(_pick_biased(pool, rng.random(), cfg.p_shaw))
    return D


DESTROY_OPS = {
    "random": _destroy_random,
    "worst": _destroy_worst,
    "related": _destroy_related,
}


def destroy(s, op: str, cfg, prob_info: dict, pre, rng):
    """(partial_bay, D) 반환."""
    n = len(s.bay)
    q = max(1, min(n, round(cfg.xi * n)))
    D = DESTROY_OPS[op](s, q, cfg, prob_info, pre, rng)
    partial_bay = list(s.bay)
    for i in D:
        partial_bay[i] = None
    return partial_bay, D
