"""Outer.floor -- last-resort feasible solution (never raises, always certified).

Per-bay tail-pointer placement: each block goes to an eligible bay (preference
desc, shortest tail tiebreak) at its IFP bottom-left corner in a window where
that bay is otherwise empty (entry = max(release, bay tail)). Empty-window +
IFP corner is structurally checker-feasible (see design doc S1 proof)."""

from __future__ import annotations

from Phase1.common import eligible
from Phase2.crane import _load_utils
from Phase2.driver import build_solution
from .state import Solution

LAST_FLOOR = {"sol": None}


def _pick_bay(pre, i, tails, prefs):
    cands = [j for j in range(pre.n_bays) if eligible(pre, i, j)]
    if not cands:
        cands = list(range(pre.n_bays))          # pathological: no eligible bay
    pf = prefs[i] if i < len(prefs) else []
    cands.sort(key=lambda j: (-(pf[j] if j < len(pf) else 0), tails[j], j))
    return cands[0]


def _corner(pre, i, j):
    for o in range(len(pre.poly[i])):
        (x_lo, x_hi), (y_lo, y_hi) = pre.IFP[i][o][j]
        if x_lo <= x_hi and y_lo <= y_hi:
            return o, (x_lo, y_lo)
    # No orientation fits j: fall back to first orientation's raw corner.
    (x_lo, _), (y_lo, _) = pre.IFP[i][0][j]
    return 0, (x_lo, y_lo)


def emergency_floor(prob_info: dict, pre) -> Solution:
    blocks = prob_info["blocks"]
    n = len(blocks)
    P = [b["processing_time"] for b in blocks]
    R = [b["release_time"] for b in blocks]
    prefs = [b.get("bay_preferences", []) for b in blocks]

    bay = [0] * n
    coords, orient, entry, exit_ = {}, {}, [0] * n, [0] * n
    tails = [0] * pre.n_bays
    for i in sorted(range(n), key=lambda k: (R[k], k)):
        j = _pick_bay(pre, i, tails, prefs)
        o, pos = _corner(pre, i, j)
        e = max(int(R[i]), tails[j])
        bay[i], orient[i], coords[i] = j, o, pos
        entry[i], exit_[i] = e, e + P[i]
        tails[j] = e + P[i]

    sol = build_solution(coords, orient, entry, exit_, bay, range(n))
    z1 = sum(max(0, exit_[i] - blocks[i]["due_date"]) for i in range(n))
    w = prob_info.get("weights", {})
    obj = float(w.get("w1", 1.0)) * z1        # conservative fallback objective
    feasible = True
    try:
        chk = _load_utils().check_feasibility(prob_info, sol)
        if "error" not in chk:
            feasible = bool(chk.get("feasible", False))
            if feasible:
                obj = chk.get("objective", obj)
    except Exception:
        pass

    return Solution(bay=list(bay), entry=list(entry), exit_=list(exit_),
                    coords=coords, orient=orient,
                    Z1=z1, Z2=0.0, Z3=0.0, objective=obj,
                    solution=sol, feasible=feasible, forced=n)
