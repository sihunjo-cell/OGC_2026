"""Phase2.repair -- 2.5 실패 처리.

victim 선택은 Z1-marginal 규칙: 충돌 블록 중 하루 미뤘을 때 지연이 가장 적게
느는 것을 미룬다. safety net은 블록을 빈 bay window에 강제 배치(구조적으로 크레인/
충돌 모두 feasible)하므로 완전한 feasible 레이아웃은 항상 존재한다."""

from __future__ import annotations


def z1_marginal(k: int, exit_: list, D: list) -> int:
    """블록 k의 exit를 하루 미룰 때 늘어나는 지연."""
    return max(0, exit_[k] + 1 - D[k]) - max(0, exit_[k] - D[k])


def shift_later(victim: int, entry: list, exit_: list, P: list, stage: int) -> None:
    """블록을 하루 미룬다 (in place).

    stage 3 (exit 막힘)     -> 체류 연장 (EXIT += 1); 반출까지 하루 더 대기.
    그 외 (entry / 공간)    -> 하루 늦게 반입 (ENTRY += 1, EXIT 재계산).
    """
    if stage == 3:
        exit_[victim] += 1
    else:
        entry[victim] += 1
        exit_[victim] = entry[victim] + P[victim]


def empty_bay_entry(schedule: list, r_time: int, proc: int) -> int:
    """[entry, entry+proc) 동안 bay가 비는, r_time 이상의 가장 이른 entry.

    schedule = 현재 bay에 있는 다른 블록들의 (entry, exit) 리스트.
    겹치는 슬롯을 지날 때까지 반복해서 밀어낸다 (매 패스마다 더 늦은 슬롯 끝으로
    전진하므로 종료)."""
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


def force_place(i: int, j: int, other_schedule: list, pre, R: list, P: list) -> tuple:
    """bay j에 블록 i를 넣는 safety-net 배치: 처음 들어맞는 orientation의 IFP
    좌하단 코너, 빈 bay window에 반입.

    반환 (pos, o, entry, exit_). bay j에서 어떤 orientation도 안 맞으면 예외
    (일어나면 안 됨: 들어맞도록 bay를 고른 것)."""
    for o in range(len(pre.poly[i])):
        (x_lo, x_hi), (y_lo, y_hi) = pre.IFP[i][o][j]
        if x_lo <= x_hi and y_lo <= y_hi:
            entry = empty_bay_entry(other_schedule, R[i], P[i])
            return (x_lo, y_lo), o, entry, entry + P[i]
    raise ValueError(f"force_place: block {i} fits no orientation in bay {j}")
