"""
Outer.alns -- ALNS driver over bay assignments.
"""

from __future__ import annotations

import dataclasses
import time
from random import Random

from Phase1 import BuildBayAssignment
from .acceptance import accept, init_temperature
from .config import OuterConfig
from .destroy import destroy
from .operators import AOS
from .realize import realize
from .repair import repair
from .cyclex import propose_cyclex
from .inbay import propose_inbay


def alns(prob_info: dict, pre, budget_s: float = None, cfg: OuterConfig = None, log=None,
         max_iters=None, deadline=None, deadline_s=None, t0=None, on_best=None):
    """bay 배정 공간 ALNS (budget/deadline/max_iters까지; stats는 진단용)."""
    cfg = cfg or OuterConfig()
    rng = Random(cfg.seed)

    p1 = BuildBayAssignment(prob_info, pre, cfg.phase1, deadline=deadline)
    start = time.perf_counter()
    if t0 is None:
        t0 = start
    budget_deadline = None if budget_s is None else (start + budget_s)

    s = realize(p1.bay, prob_info, pre, cfg.phase2, deadline=deadline)
    s_best = s

    # 증분 best 방출 (>=3s 스로틀)
    _last_emit = [0.0]

    def _emit_best(sol):
        if on_best is None or not sol.feasible:
            return
        now = time.perf_counter()
        if _last_emit[0] == 0.0 or now - _last_emit[0] >= 3.0:
            _last_emit[0] = now
            try:
                on_best(sol.objective, sol.solution)
            except Exception:
                pass

    _emit_best(s_best)

    T = init_temperature(s.objective, cfg.w_pct)
    aos = AOS(cfg.destroy_ops, cfg.repair_ops, cfg)
    aos.mark_visited(s)

    stats = {
        "iters": 0,
        "accepted": 0,
        "improved": 0,
        "restarts": 0,
        "cyclex": 0,
        "cyclex_realized": 0,
        "cyclex_improved": 0,
        "cyclex_proxy_negative": 0,
        "cyclex_proxy_hit": 0,
        "cyclex_proxy_miss": 0,
        "cyclex_proxy_best": None,
        "cyclex_real_best": None,
        "cyclex_nodes_sum": 0,
        "cyclex_checked_sum": 0,
        "cyclex_disabled": 0,
        "cyclex_time_s": 0.0,
        "inbay": 0,
        "inbay_realized": 0,
        "inbay_improved": 0,
        "inbay_real_best": None,
        "inbay_time_s": 0.0,
        "f0": s.objective,
        "f_best": s_best.objective,
        "elapsed_s": 0.0,
        "deadline_s": deadline_s,
        "stopped_by_deadline": False,
        "best_events": [[time.perf_counter() - t0, s.objective]],
        "iter_t": [],
    }
    # 정체 재시작 (κ-지터, s_best 유지)
    restart_stall = int(getattr(cfg, "restart_stall", 0) or 0)
    cyclex_stall = int(getattr(cfg, "cyclex_stall", 0) or 0)
    inbay_stall = int(getattr(cfg, "inbay_stall", 0) or 0)
    since_cyclex = 0
    since_inbay = 0
    since_best = 0
    base_kappa = float(cfg.phase2.atc_kappa) if cfg.phase2 is not None else 2.0

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
        bay2 = repair(partial, D, op_ins, cfg, prob_info, pre, rng, deadline=deadline)

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
            since_best = 0
            since_cyclex = 0
            since_inbay = 0
            _emit_best(s_best)
        else:
            since_best += 1
            since_cyclex += 1
            since_inbay += 1

        # cycle-exchange stall probe: proxy proposes, realize decides.
        if (cyclex_stall and since_cyclex >= cyclex_stall
                and not (deadline is not None and time.perf_counter() >= deadline)):
            t_cx = time.perf_counter()
            cands, cxinfo = propose_cyclex(s_best, prob_info, pre, cfg, rng, deadline=deadline)
            stats["cyclex"] += 1
            stats["cyclex_nodes_sum"] += int(cxinfo.get("nodes", 0) or 0)
            stats["cyclex_checked_sum"] += int(cxinfo.get("checked", 0) or 0)
            proxy_delta = cxinfo.get("proxy_delta")
            if proxy_delta is not None:
                stats["cyclex_proxy_negative"] += 1
                if stats["cyclex_proxy_best"] is None or proxy_delta < stats["cyclex_proxy_best"]:
                    stats["cyclex_proxy_best"] = proxy_delta
            since_cyclex = 0
            best_cx = None
            best_real_delta = None
            before_obj = s_best.objective
            for cand in cands:
                if deadline is not None and time.perf_counter() >= deadline:
                    break
                stats["cyclex_realized"] += 1
                sx = realize(cand["bay"], prob_info, pre, cfg.phase2, deadline=deadline)
                real_delta = sx.objective - before_obj
                if stats["cyclex_real_best"] is None or real_delta < stats["cyclex_real_best"]:
                    stats["cyclex_real_best"] = real_delta
                if real_delta < 0:
                    stats["cyclex_proxy_hit"] += 1
                else:
                    stats["cyclex_proxy_miss"] += 1
                if best_real_delta is None or real_delta < best_real_delta:
                    best_real_delta = real_delta
                    best_cx = sx
            if best_cx is not None and best_real_delta is not None and best_real_delta < 0:
                s = best_cx
                s_best = best_cx
                stats["accepted"] += 1
                stats["improved"] += 1
                stats["cyclex_improved"] += 1
                stats["best_events"].append([time.perf_counter() - t0, best_cx.objective])
                since_best = 0
                since_cyclex = 0
                _emit_best(s_best)
            stats["cyclex_time_s"] += time.perf_counter() - t_cx
        # 정체 재시작 (마감 후 미진입)
        # in-bay order probe: bay vector unchanged, Phase2 queue order perturbed.
        if (inbay_stall and since_inbay >= inbay_stall and cfg.phase2 is not None
                and not (deadline is not None and time.perf_counter() >= deadline)):
            t_ib = time.perf_counter()
            cands_ib = propose_inbay(s_best, prob_info, pre, cfg)
            stats["inbay"] += 1
            since_inbay = 0
            best_ib = None
            best_ib_delta = None
            before_obj = s_best.objective
            for cand in cands_ib:
                if deadline is not None and time.perf_counter() >= deadline:
                    break
                p2i = dataclasses.replace(cfg.phase2, dispatch_order_hint=cand["hint"])
                si = realize(s_best.bay, prob_info, pre, p2i, deadline=deadline)
                stats["inbay_realized"] += 1
                real_delta = si.objective - before_obj
                if stats["inbay_real_best"] is None or real_delta < stats["inbay_real_best"]:
                    stats["inbay_real_best"] = real_delta
                if best_ib_delta is None or real_delta < best_ib_delta:
                    best_ib_delta = real_delta
                    best_ib = si
            if best_ib is not None and best_ib_delta is not None and best_ib_delta < 0:
                s = best_ib
                s_best = best_ib
                stats["accepted"] += 1
                stats["improved"] += 1
                stats["inbay_improved"] += 1
                stats["best_events"].append([time.perf_counter() - t0, best_ib.objective])
                since_best = 0
                since_cyclex = 0
                since_inbay = 0
                _emit_best(s_best)
            stats["inbay_time_s"] += time.perf_counter() - t_ib
        if (restart_stall and since_best >= restart_stall and cfg.phase2 is not None
                and not (deadline is not None and time.perf_counter() >= deadline)):
            jk = min(6.0, max(0.3, base_kappa * rng.choice((0.4, 0.6, 1.5, 2.5))))
            p2j = dataclasses.replace(cfg.phase2, atc_kappa=jk)
            sj = realize(p1.bay, prob_info, pre, p2j, deadline=deadline)
            if sj.feasible:
                s = sj
                if sj.objective < s_best.objective:
                    s_best = sj
                    stats["best_events"].append([time.perf_counter() - t0, sj.objective])
                    _emit_best(s_best)
                T = init_temperature(s.objective, cfg.w_pct)
            stats["restarts"] += 1
            since_best = 0

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
    return s_best, stats
