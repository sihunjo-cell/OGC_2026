"""Phase2.contract -- 페이즈 간 데이터 계약: Phase1Output(입력), Phase2Result(출력)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Any


@dataclass
class Phase1Output:
    """Phase 1 출력: bay 배정 + 잠정 타이밍 (Z2/Z3는 배정으로 고정)."""
    bay: list
    entry: list
    exit_: list
    Z1: Optional[float] = None      # 잠정 지연; 최종 Z1은 Phase 2에서 나옴
    Z2: Optional[float] = None
    Z3: Optional[float] = None


@dataclass
class Phase2Result:
    """Phase 2 출력: 제출 dict + 배치/타이밍 + 실현 bay(재라우팅 반영) + 진단 info."""
    status: str
    solution: Optional[dict]
    coords: dict
    orient: dict
    entry: list
    exit_: list
    Z1: Optional[float]
    bay: Optional[list] = None
    info: Any = field(default_factory=dict)
