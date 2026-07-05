"""
Phase1.dff -- 2차원 면적 필요조건용 Dual Feasible Function.

아이템 (w_i, h_i)들이 W x H bay에 들어가려면 어떤 DFF 쌍 (U^p, U^q)에 대해
sum_i U^p(w_i/W)*U^q(h_i/H) <= 1 이어야 한다. 값이 1을 넘으면 동시 배치가
불가능함이 증명된다. (0, 0.5] 위 (p, q) 그리드로 Fekete-Schepers U^(eps) DFF를
쓴다 (eps=0이면 단순 면적).
"""

from __future__ import annotations


def U_eps(x: float, eps: float) -> float:
    if eps <= 0.0:
        return x
    if x > 1.0 - eps:
        return 1.0
    if x < eps:
        return 0.0
    return x


def dff_grid(step: float) -> list:
    """(0, 0.5] 그리드에 0.0(항등) 추가."""
    grid = [0.0]
    e = step
    while e <= 0.5 + 1e-9:
        grid.append(round(e, 6))
        e += step
    return grid


def item_alpha(w: float, h: float, W: float, H: float, p: float, q: float) -> float:
    if W <= 0 or H <= 0:
        return 0.0
    return U_eps(w / W, p) * U_eps(h / H, q)


def dff_lb(items: list, W: float, H: float, step: float) -> float:
    """(p, q) 그리드 전체에서 sum_i U^p(w_i/W) U^q(h_i/H)의 최댓값. 값이 1을 넘으면
    아이템들이 동시에 들어갈 수 없음이 증명된다 (필요조건 위반)."""
    grid = dff_grid(step)
    best = 0.0
    for p in grid:
        for q in grid:
            s = 0.0
            for (w, h) in items:
                s += item_alpha(w, h, W, H, p, q)
            if s > best:
                best = s
    return best
