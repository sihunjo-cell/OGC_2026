"""배정 수준 목적함수 조각 (Z2 원값은 Phase1.common.max_imbalance)."""

from __future__ import annotations


def loads_from_bay(bay: list, L: list, m: int) -> list:
    loads = [0.0] * m
    for i, j in enumerate(bay):
        if j is not None:
            loads[j] += L[i]
    return loads


def block_cost(i: int, s, w1: float, w3: float, D: list, Smax: list, S: list) -> float:
    """블록의 목적함수 직접 기여 근사 (worst destroy용)."""
    tard = max(0, s.exit_[i] - D[i])
    pref = Smax[i] - S[i][s.bay[i]]
    return w1 * tard + w3 * pref
