"""Phase1.common -- Phase 0 출력(PRE)과 prob_info 위에서 쓰는 공용 헬퍼.

bay 배정 단계에선 orientation을 확정하지 않으므로, 면적/DFF 체크용으로는
최소 면적 orientation 하나를 대표 footprint로 쓴다.
"""

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
    """블록 i가 최소 한 orientation으로 bay j에 들어가면 True (IFP 비어있지 않음)."""
    for o in range(len(pre.IFP[i])):
        (x_lo, x_hi), (y_lo, y_hi) = pre.IFP[i][o][j]
        if x_lo <= x_hi and y_lo <= y_hi:
            return True
    return False


def assign_orientation(pre, i: int) -> int:
    """bounding-box 면적이 가장 작은 orientation (가장 타이트한 표현)."""
    best_o, best_a = 0, None
    for o in range(len(pre.bbox[i])):
        x0, y0, x1, y1 = pre.bbox[i][o]
        a = (x1 - x0) * (y1 - y0)
        if best_a is None or a < best_a:
            best_a, best_o = a, o
    return best_o


def block_wh(pre, i: int) -> tuple:
    """DFF 체크용 블록 i의 대표 (width, height) bounding box."""
    o = assign_orientation(pre, i)
    x0, y0, x1, y1 = pre.bbox[i][o]
    return (x1 - x0, y1 - y0)


def footprint_area(pre, i: int) -> float:
    """블록 i의 바닥 투영 면적 (가장 큰 layer 폴리곤, 타이트한 orientation 기준)."""
    o = assign_orientation(pre, i)
    layers = pre.poly[i][o]
    if not layers:
        return 0.0
    return max(_shoelace(layer) for layer in layers)


def time_overlap(entry, exit_, a: int, b: int) -> bool:
    return entry[a] < exit_[b] and entry[b] < exit_[a]
