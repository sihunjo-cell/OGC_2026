# myalgorithm.py -- OGC 2026 solver entry point.

import time

from Outer.portfolio import default_portfolio, optimize_portfolio


ALNS_DEADLINE_S = 53.0  # FIXME
SCORING_PROFILE = "forced_risk_20"  # FIXME: tune the submission scoring profile after multi-instance sweep. check Phase2/config.py for scoring profile tuning parameters.


def algorithm(prob_info, timelimit=60):
    """Submission entry point."""
    start_time = time.perf_counter()
    deadline = start_time + ALNS_DEADLINE_S  # FIXME

    try:
        sol = optimize_portfolio(
            prob_info,
            ALNS_DEADLINE_S,
            configs=default_portfolio(scoring_profile=SCORING_PROFILE),
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

    pre = preprocess(prob_info)
    s, _ = alns(
        prob_info,
        pre,
        cfg=default_portfolio(scoring_profile=SCORING_PROFILE)[0],
        deadline=deadline,
        deadline_s=ALNS_DEADLINE_S,
    )
    return s.solution
