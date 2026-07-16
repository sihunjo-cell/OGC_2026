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


def alns(prob_info: dict, pre, budget_s: float = None, cfg: OuterConfig = None, log=None,
         max_iters=None, deadline=None, deadline_s=None, t0=None, on_best=None):
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

    # 증분 결과 방출(원자적 기록용). 전역 best 갱신 시에만, ≥3s 스로틀 -- 개선은
    # 희소하므로(첫 1~6회 후 near-miss 장벽) 방출 수는 극소, 궤적 교란 무시 가능.
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
    aos.visited.add(s.key())

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
        # anytime 곡선용: [t0 기준 상대시각(s), 그 시점의 incumbent objective].
        # 첫 원소 = 초기 realize 완료 시점(= 최초로 반환 가능한 인증해).
        "best_events": [[time.perf_counter() - t0, s.objective]],
        "iter_t": [],   # 반복 완료 시각(t0 기준). diff -> 반복 1회 비용 분포.
    }
    # 정체 재시작(restart_stall>0): κ-지터 구성으로 새 basin 탐색(s_best는 유지).
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
            _emit_best(s_best)
        else:
            since_best += 1

        # 정체 재시작: κ-지터 구성으로 새 basin (s_best 유지, 온도 리셋).
        # 마감을 이미 넘겼으면 두 번째 full realize를 시작하지 않는다(마감 후 전용).
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
