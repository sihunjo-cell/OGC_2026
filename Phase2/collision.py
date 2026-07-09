"""Phase2.collision -- 2.3 collision oracle + place_block.

collision_oracle: AABB 사전 검사 후 ring에 대해 point-in-NFP 검사.
place_block: orientation x 후보 위치를 전부 돌며 oracle이 거른 걸 빼고, 살아남은
것들을 점수 매겨 최고점 배치를 반환."""

from __future__ import annotations

from . import geometry_query as gq
from .candidates import candidate_positions
from .scoring import (_contact_exact_top_k, _score_contact_exact,
                      _score_contact_fast)


def collision_oracle(i, o, pos_i, n, on, pos_n, pre, bbox_i=None, bbox_n=None) -> bool:
    """(o, pos_i)의 블록 i가 (on, pos_n)의 블록 n과 충돌 없으면 True, 공유 layer
    중 하나라도 겹치면 False.

    먼저 AABB Test 1, 그다음 공유 layer마다 offset d = pos_i - pos_n 이
    NFP(i vs n) 내부에 있는지 검사.

    bbox_i / bbox_n은 미리 계산된 world bbox (선택). None이면 여기서 계산
    (넘겨주면 재계산만 피하는 것)."""
    if bbox_i is None:
        bbox_i = gq.world_bbox(pre, i, o, pos_i)
    if bbox_n is None:
        bbox_n = gq.world_bbox(pre, n, on, pos_n)
    if not gq.bbox_overlap(bbox_i, bbox_n):
        return True                                     # Test 1: 박스 분리 -> feasible
    dx = pos_i[0] - pos_n[0]
    dy = pos_i[1] - pos_n[1]
    kmax = min(gq.num_layers(pre, i, o), gq.num_layers(pre, n, on))
    for k in range(kmax):
        rings = gq.relative_nfp(pre, moving=i, fixed=n, o_m=o, o_f=on, k=k)
        if gq.point_in_rings(dx, dy, rings):
            return False                                # layer k에서 내부 겹침
    return True


def _collision_free(i, o, pos, residents, coords, orient, pre, resident_bbox=None) -> bool:
    # moving 쪽 world_bbox는 resident에 무관하므로 한 번만 계산 (hoist).
    bbox_i = gq.world_bbox(pre, i, o, pos)
    rb = resident_bbox
    for n in residents:
        bbox_n = rb.get(n) if rb is not None else None      # 미리 계산된 resident bbox (없으면 재계산)
        if not collision_oracle(i, o, pos, n, orient[n], coords[n], pre, bbox_i, bbox_n):
            return False
    return True


class Placement:
    __slots__ = ("score", "pos", "o")

    def __init__(self, score, pos, o):
        self.score = score
        self.pos = pos
        self.o = o


def place_block(i, j, residents, coords, orient, entry, exit_,
                prob_info, pre, cfg, m, G) -> "Placement | None":
    """bay j에서 블록 i의 충돌 없는 최적 배치(placement_score 최대), 충돌 없는
    후보 위치가 없으면 None.

    residents : bay j에 이미 배치됐고 i와 시간상 겹치는 block id들.
    m, G      : bay j에 지금까지 배치된 수, bay j 전체 블록 수 (S-curve용).
    """
    best = None
    scored_fast = []
    # resident world bbox는 이 place_block 안에서 고정 -> 한 번만 미리 계산.
    resident_bbox = {n: gq.world_bbox(pre, n, orient[n], coords[n]) for n in residents}
    for o in range(gq.num_orientations(pre, i)):
        for pos in candidate_positions(i, o, j, residents, coords, orient, pre, cfg):
            if not _collision_free(i, o, pos, residents, coords, orient, pre, resident_bbox):
                continue
            ctx = (i, o, pos, j, residents, coords, orient, exit_,
                   prob_info, pre, cfg, m, G)
            fast_score = _score_contact_fast(ctx)
            scored_fast.append((fast_score, pos, o, ctx))
    if not scored_fast:
        return None
    scored_fast.sort(key=lambda item: item[0], reverse=True)
    for _, pos, o, ctx in scored_fast[:_contact_exact_top_k(cfg)]:
        s = _score_contact_exact(ctx)
        if best is None or s > best.score:
            best = Placement(s, pos, o)
    return best
