"""Phase2.residents -- 2.0: 날짜별 residency 집합 SB[t]와 극대 시점 TT.
TT에서만 충돌 검사해도 충분하다: 시간상 겹치는 모든 쌍은 어떤 극대 residency
집합을 공유하므로."""

from __future__ import annotations


def resident_sets(block_ids: list, entry: list, exit_: list) -> tuple:
    """entry/exit_ 타이밍 배열로 block_ids의 (SB, TT) 반환.

    SB : dict[int, list[int]]  -- SB[t] = day t에 존재하는 블록들 (ENTRY <= t < EXIT).
    TT : list[int]             -- 극대 시점, 오름차순.
    """
    if not block_ids:
        return {}, []
    lo = min(entry[i] for i in block_ids)
    hi = max(exit_[i] for i in block_ids)
    # residency 집합은 ENTRY 이벤트에서만 커진다. exit만 있는 날은 진부분집합이라
    # (극대가 될 수 없음) lo와 entry 날짜에서만 SB를 평가해도 같은 극대 시점이
    # 나오고, 전체 horizon 스캔을 피할 수 있다.
    days = sorted({lo} | {entry[i] for i in block_ids if lo < entry[i] < hi})
    SB: dict = {}
    for t in days:
        present = [i for i in block_ids if entry[i] <= t < exit_[i]]
        if present:
            SB[t] = present

    # 극대 시점: residency 집합이 다른 날의 진부분집합이 아닌 t만 남긴다.
    # frozenset으로 비교하고 완전 중복도 제거.
    sets = {t: frozenset(v) for t, v in SB.items()}
    TT = []
    for t, s in sets.items():
        if any(s < other for other in sets.values()):
            continue                      # 다른 날의 진부분집합 -> 극대 아님
        TT.append(t)
    # 동일한 residency 집합은 가장 이른 날만 남기고 중복 제거.
    seen = set()
    uniq = []
    for t in sorted(TT):
        s = sets[t]
        if s in seen:
            continue
        seen.add(s)
        uniq.append(t)
    return SB, uniq
