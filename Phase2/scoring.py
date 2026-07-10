"""Phase2.scoring -- 2.3 배치 점수 (클수록 좋음). 항목:
  contact  = resident와 공유하는 경계 길이 (조밀함).
  corner   = bay 중심에서의 거리 (블록을 벽쪽으로 밀어냄).
  temporal = -sum |EXIT_i - EXIT_n| over residents (비슷한 exit 선호 -> LIFO).
  premarsh = -sum, 늦게 나가는 resident와의 bbox 겹침 면적 (vertical-sweep 근사).
  wK       = S-curve 혼합. 초반엔 contact, 후반엔 corner 강조.
w_* 가중치와 S-curve로 가중합."""

from __future__ import annotations

import math

from . import geometry_query as gq


def _s_curve_weight(m: int, G: int, K: float) -> float:
    """wK = 1 - 1/(1 + exp((2m - G)/(2K))). m = bay에 이미 배치된 블록 수,
    G = bay에 배정된 전체 블록 수."""
    if K <= 0:
        return 1.0
    z = (2.0 * m - G) / (2.0 * K)
    # 극단적인 z에서 exp 오버플로 막으려 clamp
    if z > 60:
        return 1.0
    if z < -60:
        return 0.0
    return 1.0 - 1.0 / (1.0 + math.exp(z))


def _contact(i, o, pos, residents, coords, orient, pre) -> float:
    foot_i = gq.world_layers(pre, i, o, pos)[0]
    tot = 0.0
    for n in residents:
        foot_n = gq.world_layers(pre, n, orient[n], coords[n])[0]
        tot += gq.shared_edge_length(foot_i, foot_n)
    return tot


def _corner(i, o, pos, j, prob_info, pre) -> float:
    cx, cy = gq.centroid_of_bbox(gq.world_bbox(pre, i, o, pos))
    bx, by = gq.bay_center(prob_info, j)
    return math.hypot(cx - bx, cy - by)


def _temporal(i, residents, exit_) -> float:
    return -sum(abs(exit_[i] - exit_[n]) for n in residents)


def _premarsh(i, o, pos, residents, coords, orient, exit_, pre) -> float:
    bb_i = gq.world_bbox(pre, i, o, pos)
    tot = 0.0
    for n in residents:
        if exit_[n] > exit_[i]:
            tot += gq.bbox_overlap_area(bb_i, gq.world_bbox(pre, n, orient[n], coords[n]))
    return -tot


# -- 점수 모드 --------------------------------------------------------------

def _score_contact_fast(ctx) -> float:
    (i, o, pos, j, residents, coords, orient, exit_, prob_info, pre, cfg, m, G) = ctx
    wK = _s_curve_weight(m, G, cfg.K)
    corner = _corner(i, o, pos, j, prob_info, pre)
    temporal = _temporal(i, residents, exit_)
    return (cfg.w_cn * ((1.0 - wK) * corner)
            + cfg.w_tp * temporal)


def _score_contact_exact(ctx) -> float:
    (i, o, pos, j, residents, coords, orient, exit_, prob_info, pre, cfg, m, G) = ctx
    wK = _s_curve_weight(m, G, cfg.K)
    contact = _contact(i, o, pos, residents, coords, orient, pre)
    corner = _corner(i, o, pos, j, prob_info, pre)
    temporal = _temporal(i, residents, exit_)
    premarsh = _premarsh(i, o, pos, residents, coords, orient, exit_, pre)
    return (cfg.w_ct * (wK * contact)
            + cfg.w_cn * ((1.0 - wK) * corner)
            + cfg.w_tp * temporal
            + cfg.w_pm * premarsh)


def _score_contact(ctx) -> float:
    return _score_contact_exact(ctx)


def _contact_exact_top_k(cfg) -> int:
    k = getattr(cfg, "contact_exact_top_k", 8)
    try:
        k = int(k)
    except Exception:
        k = 8
    return max(1, k)


def placement_score(i, o, pos, j, residents, coords, orient, exit_,
                    prob_info, pre, cfg, m, G) -> float:
    """resident가 주어졌을 때 bay j의 (o, pos)에 블록 i를 놓는 것의 점수
    (클수록 좋음). m = 이 bay에 이미 배치된 수, G = bay 전체 블록 수 (S-curve용)."""
    ctx = (i, o, pos, j, residents, coords, orient, exit_, prob_info, pre, cfg, m, G)
    return _score_contact_exact(ctx)

