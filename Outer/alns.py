"""
Outer.alns -- ALNS driver over bay assignments.
"""

from __future__ import annotations

import dataclasses
import time
from collections import OrderedDict
from random import Random

from Phase1 import BuildBayAssignment
from .acceptance import accept, init_temperature
from .config import OuterConfig
from .destroy import destroy
from .operators import AOS
from .realize import realize
from .repair import repair


def _freeze_sig(value):
    """Return a stable, hashable signature for config values used by realize()."""
    if dataclasses.is_dataclass(value):
        return tuple((f.name, _freeze_sig(getattr(value, f.name)))
                     for f in dataclasses.fields(value))
    if isinstance(value, dict):
        return tuple(sorted((_freeze_sig(k), _freeze_sig(v)) for k, v in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_sig(v) for v in value)
    if isinstance(value, set):
        return tuple(sorted(_freeze_sig(v) for v in value))
    return value


def _realize_key(bay, phase2cfg):
    return tuple(bay), _freeze_sig(phase2cfg)


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

    cache_limit = max(0, int(getattr(cfg, "realize_cache_size", 0) or 0))
    realize_cache = OrderedDict()
    cache_stats = {
        "realize_cache_hits": 0,
        "realize_cache_misses": 0,
        "realize_cache_evictions": 0,
        "realize_cache_saved_s": 0.0,
    }

    def _deadline_expired():
        return deadline is not None and time.perf_counter() >= deadline

    def _realize_cached(bay, phase2cfg):
        if cache_limit <= 0 or _deadline_expired():
            return realize(bay, prob_info, pre, phase2cfg, deadline=deadline)
        key = _realize_key(bay, phase2cfg)
        cached = realize_cache.get(key)
        if cached is not None:
            sol, elapsed = cached
            realize_cache.move_to_end(key)
            cache_stats["realize_cache_hits"] += 1
            cache_stats["realize_cache_saved_s"] += elapsed
            return sol

        cache_stats["realize_cache_misses"] += 1
        t_realize = time.perf_counter()
        sol = realize(bay, prob_info, pre, phase2cfg, deadline=deadline)
        elapsed = time.perf_counter() - t_realize
        if not _deadline_expired():
            if len(realize_cache) >= cache_limit:
                realize_cache.popitem(last=False)
                cache_stats["realize_cache_evictions"] += 1
            realize_cache[key] = (sol, elapsed)
        return sol

    s = _realize_cached(p1.bay, cfg.phase2)
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

        s2 = _realize_cached(bay2, cfg.phase2)

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
            _emit_best(s_best)
        else:
            since_best += 1

        # 정체 재시작 (마감 후 미진입)
        if (restart_stall and since_best >= restart_stall and cfg.phase2 is not None
                and not (deadline is not None and time.perf_counter() >= deadline)):
            jk = min(6.0, max(0.3, base_kappa * rng.choice((0.4, 0.6, 1.5, 2.5))))
            p2j = dataclasses.replace(cfg.phase2, atc_kappa=jk)
            sj = _realize_cached(p1.bay, p2j)
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
    stats.update(cache_stats)
    stats["realize_cache_size"] = len(realize_cache)
    return s_best, stats
