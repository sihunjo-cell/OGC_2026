"""
Outer.alns -- ALNS driver over bay assignments.
"""

from __future__ import annotations

import time
from random import Random

from Phase1 import BuildBayAssignment
from .acceptance import accept, init_temperature
from .config import OuterConfig
from .destroy import destroy
from .operators import AOS
from .realize import realize
from .repair import repair


def alns(prob_info: dict, pre, budget_s: float = None, cfg: OuterConfig = None, log=None,
         max_iters=None, deadline=None, deadline_s=None, t0=None):
    """Run ALNS until budget, deadline, or max_iters is reached.

    t0: anytime 계측의 시각 원점(perf_counter 값). 미지정 시 alns 시작 시각.
    stats["best_events"]/["iter_t"]는 진단용 추가 데이터로, 호출자가 무시하면
    기존 동작과 완전히 동일하다(worker/portfolio는 stats를 버림)."""
    cfg = cfg or OuterConfig()
    rng = Random(cfg.seed)

    p1 = BuildBayAssignment(prob_info, pre, cfg.phase1)
    start = time.perf_counter()
    if t0 is None:
        t0 = start
    budget_deadline = None if budget_s is None else (start + budget_s)

    s = realize(p1.bay, prob_info, pre, cfg.phase2, deadline=deadline)
    s_best = s

    T = init_temperature(s.objective, cfg.w_pct)
    aos = AOS(cfg.destroy_ops, cfg.repair_ops, cfg)
    aos.visited.add(s.key())

    stats = {
        "iters": 0,
        "accepted": 0,
        "improved": 0,
        "f0": s.objective,
        "f_best": s_best.objective,
        "traj": [],
        "elapsed_s": 0.0,
        "deadline_s": deadline_s,
        "stopped_by_deadline": False,
        # anytime 곡선용: [t0 기준 상대시각(s), 그 시점의 incumbent objective].
        # 첫 원소 = 초기 realize 완료 시점(= 최초로 반환 가능한 인증해).
        "best_events": [[time.perf_counter() - t0, s.objective]],
        "iter_t": [],   # 반복 완료 시각(t0 기준). diff -> 반복 1회 비용 분포.
    }

    it = 0
    if deadline is not None and time.perf_counter() >= deadline:
        stats["stopped_by_deadline"] = True

    while not stats["stopped_by_deadline"]:
        now = time.perf_counter()
        if deadline is not None and now >= deadline:
            stats["stopped_by_deadline"] = True
            break
        if budget_deadline is not None and now >= budget_deadline:
            break
        if max_iters is not None and it >= max_iters:
            break

        op_rem, op_ins = aos.select(rng)
        partial, D = destroy(s, op_rem, cfg, prob_info, pre, rng)
        bay2 = repair(partial, D, op_ins, cfg, prob_info, pre, rng)

        now = time.perf_counter()
        if deadline is not None and now >= deadline:
            stats["stopped_by_deadline"] = True
            break
        if budget_deadline is not None and now >= budget_deadline:
            break

        s2 = realize(bay2, prob_info, pre, cfg.phase2, deadline=deadline)

        accepted = accept(s2.objective, s.objective, T, rng)
        aos.score_update(s2, s, s_best, op_rem, op_ins, accepted)
        if accepted:
            s = s2
            stats["accepted"] += 1
        if s2.objective < s_best.objective:
            s_best = s2
            stats["improved"] += 1
            stats["best_events"].append([time.perf_counter() - t0, s2.objective])

        stats["traj"].append(s2.objective)
        stats["iter_t"].append(time.perf_counter() - t0)
        T *= cfg.c
        it += 1
        if it % cfg.seg == 0:
            aos.update_weights()
        if log and it % max(1, cfg.seg) == 0:
            log(f"iter={it} f={s.objective:.0f} best={s_best.objective:.0f} "
                f"T={T:.3g} Wrem={ {k: round(v, 2) for k, v in aos.W_rem.items()} }")

    stats["iters"] = it
    stats["f_best"] = s_best.objective
    stats["elapsed_s"] = time.perf_counter() - start
    phase2_info = dict(getattr(s_best, "phase2_info", {}) or {})
    scoring_metrics = dict(phase2_info.get("scoring_metrics", {}) or {})
    scoring_metrics.update({
        "profile": phase2_info.get("scoring_profile"),
        "objective": s_best.objective,
        "Z1": s_best.Z1,
        "forced": getattr(s_best, "forced", None),
        "iters": it,
        "iters_per_sec": (it / stats["elapsed_s"]) if stats["elapsed_s"] > 0 else 0.0,
        "ms_per_realize": (stats["elapsed_s"] / it * 1000.0) if it else None,
    })
    phase2_info["scoring_metrics"] = scoring_metrics
    s_best.phase2_info = phase2_info
    return s_best, stats


def optimize(prob_info: dict, pre, time_limit: float, cfg: OuterConfig = None):
    """Return the best solution as an operations dict."""
    s_best, _ = alns(prob_info, pre, time_limit, cfg)
    return s_best.solution
