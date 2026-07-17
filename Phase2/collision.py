"""쌍별 정확 충돌 판정 (NFP 기반)."""

from __future__ import annotations

from . import geometry_query as gq


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
