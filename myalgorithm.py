# myalgorithm.py -- OGC 2026 solver entry point.

import os
import time

from Outer.portfolio import optimize_portfolio


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

    # deadline이 이미 소진된 상태에서 전체 파이프라인을 재실행하면 timelimit 초과로
    # 프로세스가 kill된다(대형 문제 사망 경로). 재실행 대신 이미 만들어 둔 floor 반환.
    from Outer.floor import LAST_FLOOR
    if LAST_FLOOR["sol"] is not None:
        return LAST_FLOOR["sol"]

    # floor가 아예 안 만들어짐(preprocess 자체 실패): 최후의 경량 재생성.
    try:
        from Phase0 import preprocess
        from Outer.floor import emergency_floor
        pre = preprocess(prob_info)
        return emergency_floor(prob_info, pre).solution
    except Exception:
        return None
