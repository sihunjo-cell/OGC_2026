"""Phase 2 placement scoring."""

from __future__ import annotations

import math

from . import geometry_query as gq
from .crane import crane_blocks_resident


def _s_curve_weight(m: int, G: int, K: float) -> float:
    if K <= 0:
        return 1.0
    z = (2.0 * m - G) / (2.0 * K)
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


def _forced_risk_enabled(cfg) -> bool:
    return getattr(cfg, "forced_risk_weight", 0.0) > 0.0 and getattr(cfg, "forced_risk_mode", "off") != "off"


def _forced_risk_mode(cfg) -> str:
    return getattr(cfg, "forced_risk_mode", "off")


def _forced_risk_max_residents(cfg) -> int:
    try:
        return max(1, int(getattr(cfg, "forced_risk_max_residents", 6)))
    except Exception:
        return 6


def _bbox_area(bb) -> float:
    return max(0.0, (bb[1] - bb[0]) * (bb[3] - bb[2]))


def _risk_urgency(exit_i: float, exit_n: float) -> float:
    gap = max(0.0, float(exit_i - exit_n))
    # FIXME: forced-risk urgency decay should be tuned with scoring profile sweep.
    return 1.0 / (1.0 + gap)


def _forced_risk_proxy(ctx, stage: str) -> float:
    (i, o, pos, j, residents, coords, orient, exit_, prob_info, pre, cfg, m, G) = ctx
    if not _forced_risk_enabled(cfg):
        return 0.0

    debug_stats = getattr(cfg, "_debug_variant_stats", None)
    mode = _forced_risk_mode(cfg)
    if debug_stats is not None:
        key = "forced_risk_fast_evaluations" if stage == "fast" else "forced_risk_exact_evaluations"
        debug_stats[key] = debug_stats.get(key, 0) + 1
        debug_stats["forced_risk_evaluations"] = debug_stats.get("forced_risk_evaluations", 0) + 1

    earlier = [n for n in residents if exit_[n] < exit_[i]]
    if not earlier:
        return 0.0

    earlier.sort(key=lambda n: (exit_[i] - exit_[n], exit_[n], n))
    earlier = earlier[:_forced_risk_max_residents(cfg)]

    bb_i = gq.world_bbox(pre, i, o, pos)
    area_i = max(1.0, _bbox_area(bb_i))
    risk = 0.0
    crane_checks = 0

    for n in earlier:
        bb_n = gq.world_bbox(pre, n, orient[n], coords[n])
        overlap = gq.bbox_overlap_area(bb_i, bb_n)
        overlap_norm = overlap / max(1.0, min(area_i, _bbox_area(bb_n)))
        term = overlap_norm
        if stage == "exact" and mode == "overlap_crane":
            crane_checks += 1
            if crane_blocks_resident(i, o, pos, n, coords, orient, pre):
                # FIXME: crane-blocking addend inside forced-risk scoring is experimental.
                term += 1.0
        risk += _risk_urgency(exit_[i], exit_[n]) * term

    if debug_stats is not None:
        debug_stats["forced_risk_resident_checks"] = (
            debug_stats.get("forced_risk_resident_checks", 0) + len(earlier)
        )
        if stage == "exact":
            debug_stats["forced_risk_crane_checks"] = (
                debug_stats.get("forced_risk_crane_checks", 0) + crane_checks
            )
    return risk


def _effective_weight_terms(cfg) -> tuple[float, float, float, float]:
    # FIXME: base contact/corner/temporal/premarsh weights should be tuned with scoring profile sweep.
    w_ct_eff = getattr(cfg, "w_ct", 1.0) * getattr(cfg, "contact_weight_scale", 1.0)
    w_cn_eff = getattr(cfg, "w_cn", 0.01) * getattr(cfg, "corner_weight_scale", 1.0)
    w_tp_eff = getattr(cfg, "w_tp", 0.1) * getattr(cfg, "temporal_weight_scale", 1.0)
    w_pm_eff = getattr(cfg, "w_pm", 1.0) * getattr(cfg, "premarsh_weight_scale", 1.0)
    return w_ct_eff, w_cn_eff, w_tp_eff, w_pm_eff


def _score_contact_fast(ctx) -> float:
    (i, o, pos, j, residents, coords, orient, exit_, prob_info, pre, cfg, m, G) = ctx
    wK = _s_curve_weight(m, G, cfg.K)
    _, w_cn_eff, w_tp_eff, _ = _effective_weight_terms(cfg)
    corner = _corner(i, o, pos, j, prob_info, pre)
    temporal = _temporal(i, residents, exit_)
    forced_risk = _forced_risk_proxy(ctx, stage="fast")
    return (w_cn_eff * ((1.0 - wK) * corner)
            + w_tp_eff * temporal
            - cfg.forced_risk_weight * forced_risk)


def _score_contact_exact_with_proxy(ctx) -> tuple[float, float]:
    (i, o, pos, j, residents, coords, orient, exit_, prob_info, pre, cfg, m, G) = ctx
    wK = _s_curve_weight(m, G, cfg.K)
    w_ct_eff, w_cn_eff, w_tp_eff, w_pm_eff = _effective_weight_terms(cfg)
    contact = _contact(i, o, pos, residents, coords, orient, pre)
    corner = _corner(i, o, pos, j, prob_info, pre)
    temporal = _temporal(i, residents, exit_)
    premarsh = _premarsh(i, o, pos, residents, coords, orient, exit_, pre)
    forced_risk = _forced_risk_proxy(ctx, stage="exact")
    score = (w_ct_eff * (wK * contact)
             + w_cn_eff * ((1.0 - wK) * corner)
             + w_tp_eff * temporal
             + w_pm_eff * premarsh
             - cfg.forced_risk_weight * forced_risk)
    return score, forced_risk


def _score_contact_exact(ctx) -> float:
    return _score_contact_exact_with_proxy(ctx)[0]


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
    ctx = (i, o, pos, j, residents, coords, orient, exit_, prob_info, pre, cfg, m, G)
    return _score_contact_exact(ctx)
