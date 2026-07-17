"""DFF 면적 필요조건: 값 > 1이면 동시 배치 불가 증명 (Fekete-Schepers U^eps)."""

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
    """(0, 0.5] 그리드 + 0.0(항등)."""
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
    """(p,q) 그리드 최대 DFF 합 (> 1 = 동시 배치 불가)."""
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
