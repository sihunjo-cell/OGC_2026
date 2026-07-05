"""Phase2.ordering -- 2.1 배치 순서. 'area' = lam1 * footprint 면적 기준 어려운 것
먼저. 'mst' = 최소 slack 우선(납기 반영, 면적으로 tie-break)으로 지연 감소."""

from __future__ import annotations


def area_component(i: int, o: int, pre) -> float:
    return pre.area[i][o]


def difficulty(i: int, pre, cfg, o: int = 0) -> float:
    """배치 순서에 쓰는 정적 난이도 (면적 가중)."""
    return cfg.lam1 * area_component(i, o, pre)


def static_order(block_ids: list, pre, cfg) -> list:
    """전체 배치 순서. 'area' = 정적 난이도 높은 것 먼저. 'mst' = slack 작은
    (가장 급한) 것 먼저, 면적 내림차순으로 tie-break."""
    if getattr(cfg, "order_mode", "area") == "mst":
        return sorted(block_ids, key=lambda i: (pre.slack[i], -pre.area[i][0]))
    return sorted(block_ids, key=lambda i: difficulty(i, pre, cfg), reverse=True)
