"""
Separate intra-bay local repair layer.

This module keeps bay assignments fixed. It tries to move a small number of
costly blocks to earlier feasible positions inside their current bay, then
keeps the move only if the official checker reports a better feasible solution.
"""

from __future__ import annotations

from copy import deepcopy
import time

import numpy as np

from Phase2.collision import _collision_free
from Phase2.crane import crane_blocks_resident, crane_obstructed, _load_utils
from Phase2.driver import build_solution
from Phase2.raster import Raster
from .state import Solution


def improve_intrabay(solution: Solution, prob_info: dict, pre, cfg, rng, deadline=None) -> Solution:
    """Return an intra-bay improved solution, or the original solution."""
    if not getattr(cfg, "intrabay_enabled", False):
        return solution
    if not solution.feasible or solution.solution is None:
        return solution

    try:
        utils_mod = _load_utils()
    except Exception:
        return solution

    current = solution
    moves_left = max(0, int(getattr(cfg, "intrabay_max_moves", 0)))
    if moves_left <= 0:
        return solution

    for i in _target_blocks(current, prob_info, cfg):
        if _past(deadline) or moves_left <= 0:
            break
        improved = _try_move_block_earlier(current, i, prob_info, pre, cfg, utils_mod)
        if improved is not current:
            current = improved
            moves_left -= 1
    return current


def _past(deadline) -> bool:
    return deadline is not None and time.perf_counter() >= deadline


def _target_blocks(solution: Solution, prob_info: dict, cfg) -> list[int]:
    blocks = prob_info["blocks"]
    due = [b["due_date"] for b in blocks]
    forced = set(solution.phase2_info.get("forced", []))
    forced.update(solution.phase2_info.get("forced_construction", []))

    def score(i: int):
        tardy = max(0, solution.exit_[i] - due[i])
        return (i not in forced, -tardy, solution.entry[i], i)

    ranked = sorted(range(len(blocks)), key=score)
    limit = max(0, int(getattr(cfg, "intrabay_max_targets", 0)))
    return ranked[:limit] if limit else []


def _try_move_block_earlier(solution: Solution, i: int, prob_info: dict, pre, cfg, utils_mod):
    blocks = prob_info["blocks"]
    bay = list(solution.bay)
    j = bay[i]
    proc = blocks[i]["processing_time"]
    release = blocks[i]["release_time"]
    current_entry = solution.entry[i]

    residents = [k for k, bk in enumerate(bay) if bk == j and k != i]
    times = sorted({int(release)} | {int(solution.exit_[k]) for k in residents if release <= solution.exit_[k] < current_entry})

    for t in times:
        if t >= current_entry:
            continue
        xt = t + proc
        slot = _find_position_at_time(solution, i, j, t, xt, residents, prob_info, pre, cfg)
        if slot is None:
            continue
        candidate = _build_candidate(solution, i, j, t, xt, slot, prob_info, utils_mod)
        min_gain = float(getattr(cfg, "intrabay_min_gain", 0.0))
        if candidate is not None and candidate.objective + min_gain < solution.objective:
            return candidate
    return solution


def _find_position_at_time(solution, i, j, entry, exit_, residents, prob_info, pre, cfg):
    overlap = [k for k in residents if solution.entry[k] < exit_ and entry < solution.exit_[k]]
    entry_blockers = [k for k in residents if solution.entry[k] < entry < solution.exit_[k]]
    exit_blockers = [k for k in residents if solution.entry[k] < exit_ < solution.exit_[k]]
    exiting_before_or_at = [k for k in residents if solution.exit_[k] <= exit_]

    raster = Raster(prob_info, pre)
    for k in overlap:
        raster.add(j, k, solution.orient[k], solution.coords[k])

    cap = int(getattr(cfg, "intrabay_cand_cap", 24))
    for o in range(len(pre.poly[i])):
        clipped = _clipped_scan(raster, prob_info, pre, j, i, o)
        if clipped is None:
            continue
        feas, mx0, my0 = clipped
        for r, c in raster.order_cells(j, i, o, feas, cap):
            pos = (int(c) - mx0, int(r) - my0)
            if not _collision_free(i, o, pos, overlap, solution.coords, solution.orient, pre):
                continue
            if crane_obstructed(i, o, pos, entry_blockers, solution.coords, solution.orient, pre):
                continue
            if crane_obstructed(i, o, pos, exit_blockers, solution.coords, solution.orient, pre):
                continue
            if any(crane_blocks_resident(i, o, pos, k, solution.coords, solution.orient, pre) for k in exiting_before_or_at):
                continue
            return pos, o
    return None


def _clipped_scan(raster, prob_info, pre, j, i, o):
    (x_lo, x_hi), (y_lo, y_hi) = pre.IFP[i][o][j]
    if x_lo > x_hi or y_lo > y_hi:
        return None
    feas, mx0, my0 = raster.scan(j, i, o)
    if feas.size == 0:
        return None
    allow = np.zeros_like(feas)
    r_lo, r_hi = max(0, y_lo + my0), min(feas.shape[0] - 1, y_hi + my0)
    c_lo, c_hi = max(0, x_lo + mx0), min(feas.shape[1] - 1, x_hi + mx0)
    if r_lo > r_hi or c_lo > c_hi:
        return None
    allow[r_lo:r_hi + 1, c_lo:c_hi + 1] = feas[r_lo:r_hi + 1, c_lo:c_hi + 1]
    return allow, mx0, my0


def _build_candidate(solution, i, j, entry, exit_, slot, prob_info, utils_mod):
    coords = dict(solution.coords)
    orient = dict(solution.orient)
    entries = list(solution.entry)
    exits = list(solution.exit_)
    coords[i], orient[i] = slot
    entries[i], exits[i] = entry, exit_

    sol_dict = build_solution(coords, orient, entries, exits, solution.bay, range(len(solution.bay)))
    try:
        chk = utils_mod.check_feasibility(prob_info, sol_dict)
    except Exception:
        return None
    if not chk.get("feasible", False):
        return None

    info = deepcopy(solution.phase2_info)
    info.setdefault("intrabay_moves", [])
    info["intrabay_moves"].append({"block": i, "bay": j, "entry": entry, "exit": exit_})

    return Solution(
        bay=list(solution.bay),
        entry=entries,
        exit_=exits,
        coords=coords,
        orient=orient,
        Z1=chk.get("obj1", solution.Z1),
        Z2=chk.get("obj2", solution.Z2),
        Z3=chk.get("obj3", solution.Z3),
        objective=chk.get("objective", solution.objective),
        solution=sol_dict,
        feasible=True,
        forced=solution.forced,
        phase2_info=info,
    )
