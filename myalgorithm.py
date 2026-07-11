# myalgorithm.py -- OGC 2026 solver entry point.

import os
import time

from Outer.portfolio import default_portfolio, optimize_portfolio


DEFAULT_SCORING_PROFILE = "forced_risk_20"  # FIXME: tune the default scoring profile after broader experiments.
DEFAULT_DEADLINE_SAFETY_MARGIN_S = 2.0  # FIXME: tune the default deadline safety margin.


def _resolve_scoring_profile():
    return os.environ.get("OGC_SCORING_PROFILE", DEFAULT_SCORING_PROFILE)


def _resolve_alns_deadline_s(timelimit):
    env = os.environ.get("OGC_ALNS_DEADLINE_S", "").strip()
    if env:
        try:
            return max(1.0, float(env))
        except ValueError:
            pass
    return max(1.0, float(timelimit) - DEFAULT_DEADLINE_SAFETY_MARGIN_S)


def algorithm(prob_info, timelimit=60):
    """Submission entry point."""
    scoring_profile = _resolve_scoring_profile()
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
            scoring_profile=scoring_profile,
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
        cfg=default_portfolio(scoring_profile=scoring_profile)[0],
        deadline=deadline,
        deadline_s=alns_deadline_s,
    )
    return s.solution
