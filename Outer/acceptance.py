"""SA 수용 판정 (T_start = -(w_pct*f0)/ln(0.5), Metropolis + 기하 냉각)."""

from __future__ import annotations

import math


def init_temperature(f0: float, w_pct: float) -> float:
    if f0 <= 0:
        return 1.0
    return -(w_pct * f0) / math.log(0.5)


def accept(f_new: float, f_cur: float, T: float, rng) -> bool:
    if f_new < f_cur:
        return True
    if T <= 0:
        return False
    try:
        return rng.random() < math.exp(-(f_new - f_cur) / T)
    except OverflowError:
        return False
