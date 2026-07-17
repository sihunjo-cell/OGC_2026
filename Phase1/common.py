"""Phase1 공용 헬퍼 (대표 orientation = 최소 bbox 면적)."""

from __future__ import annotations


def _shoelace(pts) -> float:
    n = len(pts)
    if n < 3:
        return 0.0
    s = 0.0
    for k in range(n):
        x1, y1 = pts[k]
        x2, y2 = pts[(k + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) * 0.5


def eligible(pre, i: int, j: int) -> bool:
    """어느 orientation으로든 bay j에 들어가면 True."""
    for o in range(len(pre.IFP[i])):
        (x_lo, x_hi), (y_lo, y_hi) = pre.IFP[i][o][j]
        if x_lo <= x_hi and y_lo <= y_hi:
            return True
    return False


def assign_orientation(pre, i: int) -> int:
    """최소 bbox 면적 orientation."""
    best_o, best_a = 0, None
    for o in range(len(pre.bbox[i])):
        x0, y0, x1, y1 = pre.bbox[i][o]
        a = (x1 - x0) * (y1 - y0)
        if best_a is None or a < best_a:
            best_a, best_o = a, o
    return best_o


def block_wh(pre, i: int) -> tuple:
    """대표 (width, height)."""
    o = assign_orientation(pre, i)
    x0, y0, x1, y1 = pre.bbox[i][o]
    return (x1 - x0, y1 - y0)


def footprint_area(pre, i: int) -> float:
    """바닥 투영 면적 (최대 layer)."""
    o = assign_orientation(pre, i)
    layers = pre.poly[i][o]
    if not layers:
        return 0.0
    return max(_shoelace(layer) for layer in layers)


def time_overlap(entry, exit_, a: int, b: int) -> bool:
    return entry[a] < exit_[b] and entry[b] < exit_[a]
