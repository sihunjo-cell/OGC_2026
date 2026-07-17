"""크레인 충돌 수리: Z1-marginal 순 하루 밀기 + 빈 창 강제 배치(항상 feasible 종착)."""

from __future__ import annotations


def z1_marginal(k: int, exit_: list, D: list) -> int:
    """exit 하루 미룰 때 지연 증가분."""
    return max(0, exit_[k] + 1 - D[k]) - max(0, exit_[k] - D[k])


def shift_later(victim: int, entry: list, exit_: list, P: list, stage: int) -> None:
    """하루 밀기: stage 3(exit 막힘)=체류 연장, 그 외=반입 지연."""
    if stage == 3:
        exit_[victim] += 1
    else:
        entry[victim] += 1
        exit_[victim] = entry[victim] + P[victim]


def empty_bay_entry(schedule: list, r_time: int, proc: int) -> int:
    """bay가 통째로 비는 가장 이른 entry (겹침 슬롯 끝으로 전진 반복 = 종료 보장)."""
    entry = int(r_time)
    changed = True
    while changed:
        changed = False
        exit_t = entry + proc
        for a, e in schedule:
            if entry < e and a < exit_t:          # 겹침
                entry = max(entry, e)
                changed = True
    return entry


def force_corner(i: int, j: int, pre) -> tuple:
    """IFP 코너 + 첫 적합 orientation (마감 후 O(1) tail-pointer용, 폴백 = 완결 보장)."""
    for o in range(len(pre.poly[i])):
        (x_lo, x_hi), (y_lo, y_hi) = pre.IFP[i][o][j]
        if x_lo <= x_hi and y_lo <= y_hi:
            return (x_lo, y_lo), o
    (x_lo, _), (y_lo, _) = pre.IFP[i][0][j]
    return (x_lo, y_lo), 0


def force_place(i: int, j: int, other_schedule: list, pre, R: list, P: list) -> tuple:
    """safety-net 배치: IFP 코너 + 빈 bay 창 -> (pos, o, entry, exit_)."""
    for o in range(len(pre.poly[i])):
        (x_lo, x_hi), (y_lo, y_hi) = pre.IFP[i][o][j]
        if x_lo <= x_hi and y_lo <= y_hi:
            entry = empty_bay_entry(other_schedule, R[i], P[i])
            return (x_lo, y_lo), o, entry, entry + P[i]
    raise ValueError(f"force_place: block {i} fits no orientation in bay {j}")
