"""
Outer.state -- ALNS Solution: 베이 배정 + 실현된 배치/타이밍 + 목적함수 분해.
key()는 방문 집합용 해시 가능한 지문.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Solution:
    bay: list                 # 블록 i의 베이 배정 bay[i]
    entry: list               # 실현된 진입시각 ENTRY[i]
    exit_: list               # 실현된 반출시각 EXIT[i]
    coords: dict              # block_id -> (x, y) 좌표
    orient: dict              # block_id -> 배치 방향
    Z1: float                 # 실현된 지연(tardiness)
    Z2: float                 # 부하 불균형(베이 배정 기준)
    Z3: float                 # 선호 페널티(베이 배정 기준)
    objective: float          # w1 Z1 + w2 Z2 + w3 Z3 (utils 목적함수와 동일)
    solution: dict            # utils/제출용 {"operations": {...}}
    feasible: bool = True
    forced: int = 0           # 뒤 윈도로 밀린 블록 수(Z1 원인, 진단용)
    phase2_info: dict = field(default_factory=dict)

    def key(self):
        return (tuple(self.bay),
                tuple(self.entry),
                tuple(self.exit_),
                tuple(self.coords.get(i) for i in range(len(self.bay))),
                tuple(self.orient.get(i) for i in range(len(self.bay))))
