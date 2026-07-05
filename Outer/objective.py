"""
Outer.objective -- repair/destroy가 Phase 2 없이 쓰는 배정 수준 목적함수 조각
(z2_raw 불균형, z3_of_bay 선호, block_cost).
"""

from __future__ import annotations


def loads_from_bay(bay: list, L: list, m: int) -> list:
    loads = [0.0] * m
    for i, j in enumerate(bay):
        if j is not None:
            loads[j] += L[i]
    return loads


def z2_raw(loads: list, u: list) -> float:
    """정규화 불균형 최대값(하한 미적용) max_{j1!=j2} |u_j1 load_j1 - u_j2 load_j2|
    = max(u*load) - min(u*load)."""
    m = len(loads)
    if m < 2:
        return 0.0
    wl = [u[j] * loads[j] for j in range(m)]
    return max(wl) - min(wl)


def z3_of_bay(bay: list, Smax: list, S: list) -> float:
    return sum(Smax[i] - S[i][bay[i]] for i in range(len(bay)) if bay[i] is not None)


def block_cost(i: int, s, w1: float, w3: float, D: list, Smax: list, S: list) -> float:
    """블록 i의 목적함수 직접 기여분(f(s) - f_{-i}(s) 근사): 지연 + 선호 페널티.
    'worst' destroy 연산자가 사용."""
    tard = max(0, s.exit_[i] - D[i])
    pref = Smax[i] - S[i][s.bay[i]]
    return w1 * tard + w3 * pref
