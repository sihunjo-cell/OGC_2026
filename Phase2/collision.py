"""Phase 2 collision checks and placement candidate ranking."""

from __future__ import annotations

from . import geometry_query as gq
from .candidates import candidate_positions
from .scoring import (_contact_exact_top_k, _score_contact_exact,
                      _score_contact_fast)


def collision_oracle(i, o, pos_i, n, on, pos_n, pre, bbox_i=None, bbox_n=None) -> bool:
    """Return True when placements of blocks i and n do not collide."""
    if bbox_i is None:
        bbox_i = gq.world_bbox(pre, i, o, pos_i)
    if bbox_n is None:
        bbox_n = gq.world_bbox(pre, n, on, pos_n)
    if not gq.bbox_overlap(bbox_i, bbox_n):
        return True
    dx = pos_i[0] - pos_n[0]
    dy = pos_i[1] - pos_n[1]
    kmax = min(gq.num_layers(pre, i, o), gq.num_layers(pre, n, on))
    for k in range(kmax):
        rings = gq.relative_nfp(pre, moving=i, fixed=n, o_m=o, o_f=on, k=k)
        if gq.point_in_rings(dx, dy, rings):
            return False
    return True


def _collision_free(i, o, pos, residents, coords, orient, pre, resident_bbox=None) -> bool:
    bbox_i = gq.world_bbox(pre, i, o, pos)
    rb = resident_bbox
    for n in residents:
        bbox_n = rb.get(n) if rb is not None else None
        if not collision_oracle(i, o, pos, n, orient[n], coords[n], pre, bbox_i, bbox_n):
            return False
    return True


class Placement:
    __slots__ = ("score", "pos", "o")

    def __init__(self, score, pos, o):
        self.score = score
        self.pos = pos
        self.o = o


def ranked_block_candidates(i, j, residents, coords, orient, entry, exit_,
                            prob_info, pre, cfg, m, G) -> list:
    scored_fast = []
    debug_stats = getattr(cfg, "_debug_variant_stats", None)
    resident_bbox = {n: gq.world_bbox(pre, n, orient[n], coords[n]) for n in residents}
    for o in range(gq.num_orientations(pre, i)):
        for pos in candidate_positions(i, o, j, residents, coords, orient, pre, cfg):
            if not _collision_free(i, o, pos, residents, coords, orient, pre, resident_bbox):
                continue
            ctx = (i, o, pos, j, residents, coords, orient, exit_,
                   prob_info, pre, cfg, m, G)
            scored_fast.append((_score_contact_fast(ctx), pos, o, ctx))
    if not scored_fast:
        if debug_stats is not None:
            debug_stats["placement_calls"] = debug_stats.get("placement_calls", 0) + 1
            debug_stats["zero_feasible_calls"] = debug_stats.get("zero_feasible_calls", 0) + 1
        return []

    scored_fast.sort(key=lambda item: item[0], reverse=True)
    top_k = _contact_exact_top_k(cfg)
    if debug_stats is not None:
        debug_stats["placement_calls"] = debug_stats.get("placement_calls", 0) + 1
        debug_stats["feasible_candidates_total"] = (
            debug_stats.get("feasible_candidates_total", 0) + len(scored_fast)
        )
        debug_stats["exact_candidates_total"] = (
            debug_stats.get("exact_candidates_total", 0) + min(len(scored_fast), top_k)
        )
        debug_stats["max_feasible_candidates"] = max(
            debug_stats.get("max_feasible_candidates", 0), len(scored_fast)
        )
        if len(scored_fast) > top_k:
            debug_stats["truncated_calls"] = debug_stats.get("truncated_calls", 0) + 1

    ranked = []
    for _, pos, o, ctx in scored_fast[:top_k]:
        ranked.append(Placement(_score_contact_exact(ctx), pos, o))
    ranked.sort(key=lambda cand: cand.score, reverse=True)
    return ranked


def place_block(i, j, residents, coords, orient, entry, exit_,
                prob_info, pre, cfg, m, G) -> "Placement | None":
    ranked = ranked_block_candidates(i, j, residents, coords, orient, entry, exit_,
                                     prob_info, pre, cfg, m, G)
    return ranked[0] if ranked else None
