"""Phase0.cliques -- 0.3: [ENTRY, EXIT) 구간 그래프의 bay별 maximal clique.
Phase 1 타이밍/배치가 필요해 preprocess() 이후 실행."""

from __future__ import annotations


def _interval_maximal_cliques(intervals: list) -> list:
    """반열린 구간 [a, e) interval graph의 maximal clique 리스트.

    intervals: (entry, exit, block_id). 모든 maximal clique는 어떤 시작 시점의
    active set이므로 시작 시점만 훑으면 충분. 정렬된 block-id 리스트로 반환.
    """
    if not intervals:
        return []
    starts = sorted({a for a, _, _ in intervals})
    snapshots = set()
    for t in starts:
        active = frozenset(i for a, e, i in intervals if a <= t < e)
        if active:
            snapshots.add(active)
    snaps = list(snapshots)
    maximal = []
    for c in snaps:
        if not any(c < d for d in snaps):   # 어떤 것의 진부분집합도 아니면 maximal
            maximal.append(sorted(c))
    return maximal


def precompute_cliques(entry: list, exit_: list, bay: list, n_bays: int) -> list:
    """같이 머무는 block들의 bay별 maximal clique.

    entry/exit_/bay는 block-id별 리스트. cliques[j] = bay j의 maximal clique
    리스트(정렬된 block-id). 홀로 있는 block은 singleton.
    """
    per_bay_intervals: list = [[] for _ in range(n_bays)]
    for i, j in enumerate(bay):
        per_bay_intervals[j].append((entry[i], exit_[i], i))
    return [_interval_maximal_cliques(per_bay_intervals[j]) for j in range(n_bays)]
