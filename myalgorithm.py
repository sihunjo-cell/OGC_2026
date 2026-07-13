# myalgorithm.py -- OGC 2026 solver entry point.

import os
import time

from Outer.portfolio import default_portfolio, optimize_portfolio


def _alns_reserve(timelimit):
    """timelimit - budget. 부모가 워커를 deadline + wrapup(8~25s)에 강제 종료하므로
    reserve > wrapup 이면 반환 < timelimit 보장(전 T에서 reserve - wrapup >= 4)."""
    return min(max(12.0, 0.06 * float(timelimit)), 30.0)


def _resolve_alns_deadline_s(timelimit):
    """deadline budget = timelimit - reserve. OGC_ALNS_DEADLINE_S env는 실험용이며
    제출 환경 유출 시 timeout(-1) 방지를 위해 budget으로 상한한다(§0-3)."""
    budget = max(5.0, float(timelimit) - _alns_reserve(timelimit))
    env = os.environ.get("OGC_ALNS_DEADLINE_S", "").strip()
    if env:
        try:
            return max(1.0, min(float(env), budget))
        except ValueError:
            pass
    return budget


def algorithm(prob_info, timelimit=60):
    """Submission entry point. deadline을 timelimit에서 유도(초과 시 -1점, §3.2)."""
    alns_deadline_s = _resolve_alns_deadline_s(timelimit)
    start_time = time.perf_counter()
    deadline = start_time + alns_deadline_s

    try:
        sol = optimize_portfolio(
            prob_info,
            alns_deadline_s,
            n_workers=4,
            deadline=deadline,
            deadline_s=alns_deadline_s,
        )
        if sol is not None:
            return sol
    except Exception:
        pass

    from Phase0 import preprocess
    from Outer import alns

    pre = preprocess(prob_info)
    s, _ = alns(
        prob_info,
        pre,
        cfg=default_portfolio()[0],
        deadline=deadline,
        deadline_s=alns_deadline_s,
    )
    return s.solution
