# myalgorithm.py -- OGC 2026 solver entry point.

import time


ALNS_DEADLINE_S = 53.0  # FIXME


def algorithm(prob_info, timelimit=60):
    """Submission entry point."""
    start_time = time.perf_counter()
    deadline = start_time + ALNS_DEADLINE_S  # FIXME

    try:
        from Outer.portfolio import optimize_portfolio

        sol = optimize_portfolio(
            prob_info,
            ALNS_DEADLINE_S,
            n_workers=4,
            deadline=deadline,
            deadline_s=ALNS_DEADLINE_S,
        )
        if sol is not None:
            return sol
    except Exception:
        pass

    from Phase0 import preprocess
    from Outer import alns
    from Outer.portfolio import default_portfolio

    pre = preprocess(prob_info)
    s, _ = alns(
        prob_info,
        pre,
        cfg=default_portfolio()[0],
        deadline=deadline,
        deadline_s=ALNS_DEADLINE_S,
    )
    return s.solution
