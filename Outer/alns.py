"""
Outer.alns -- ALNS 구동부: 초기 배정 후 destroy -> repair -> realize -> SA 수용을
적응적 연산자 가중치로 시간 제한까지 반복. 탐색 공간은 베이 배정이고
realize()가 각각을 정확히 평가.
"""

from __future__ import annotations

import time
from random import Random

from Phase1 import BuildBayAssignment
from .config import OuterConfig
from .realize import realize
from .destroy import destroy
from .repair import repair
from .operators import AOS
from .acceptance import init_temperature, accept


def alns(prob_info: dict, pre, time_limit: float, cfg: OuterConfig = None, log=None,
         max_iters=None):
    """ALNS를 time_limit초(또는 max_iters, 먼저 도달하는 쪽)까지 실행하고
    (s_best, stats) 반환."""
    cfg = cfg or OuterConfig()
    rng = Random(cfg.seed)

    # -- 초기 실행가능해 -------------------------------------------------------
    p1 = BuildBayAssignment(prob_info, pre, cfg.phase1)
    t0 = time.time()
    deadline = t0 + time_limit
    s = realize(p1.bay, prob_info, pre, cfg.phase2, deadline=deadline)
    s_best = s

    T = init_temperature(s.objective, cfg.w_pct)
    aos = AOS(cfg.destroy_ops, cfg.repair_ops, cfg)
    aos.visited.add(s.key())

    stats = {"iters": 0, "accepted": 0, "improved": 0,
             "f0": s.objective, "f_best": s_best.objective, "traj": []}

    it = 0
    while time.time() < deadline and (max_iters is None or it < max_iters):
        op_rem, op_ins = aos.select(rng)
        partial, D = destroy(s, op_rem, cfg, prob_info, pre, rng)
        bay2 = repair(partial, D, op_ins, cfg, prob_info, pre, rng)
        s2 = realize(bay2, prob_info, pre, cfg.phase2, deadline=deadline)

        accepted = accept(s2.objective, s.objective, T, rng)
        aos.score_update(s2, s, s_best, op_rem, op_ins, accepted)
        if accepted:
            s = s2
            stats["accepted"] += 1
        if s2.objective < s_best.objective:
            s_best = s2
            stats["improved"] += 1

        stats["traj"].append(s2.objective)     # 관측용(반복별 후보 f)
        T *= cfg.c
        it += 1
        if it % cfg.seg == 0:
            aos.update_weights()
        if log and it % max(1, cfg.seg) == 0:
            log(f"iter={it} f={s.objective:.0f} best={s_best.objective:.0f} "
                f"T={T:.3g} Wrem={ {k: round(v,2) for k,v in aos.W_rem.items()} }")

    stats["iters"] = it
    stats["f_best"] = s_best.objective
    return s_best, stats


def optimize(prob_info: dict, pre, time_limit: float, cfg: OuterConfig = None):
    """s_best의 제출용 operations dict를 반환하는 편의 래퍼."""
    s_best, _ = alns(prob_info, pre, time_limit, cfg)
    return s_best.solution
