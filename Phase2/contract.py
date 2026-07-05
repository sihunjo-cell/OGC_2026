"""Phase2.contract -- 페이즈 간 데이터 계약: Phase1Output(입력), Phase2Result(출력)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Any


@dataclass
class Phase1Output:
    """Phase 1의 배정 + 잠정 타이밍 (리스트 필드는 block id로 0-인덱싱).

      bay     : 배정된 bay id (Z2, Z3 고정).
      entry   : ENTRY_i (잠정, = EST_i).
      exit_   : EXIT_i (잠정, = entry_i + P_i).
      cliques : cliques[j] -> 극대 clique들 (각각 정렬된 block-id 리스트).
      Z2, Z3  : bay 배정으로 고정되는 목적함수 성분 (참고용).
    """
    bay: list
    entry: list
    exit_: list
    cliques: list
    Z1: Optional[float] = None      # 잠정 지연; 최종 Z1은 Phase 2에서 나옴
    Z2: Optional[float] = None
    Z3: Optional[float] = None


@dataclass
class Phase2Result:
    """Phase 2 출력.

      status      : "SOLUTION" (완전한 feasible 레이아웃) 또는 "CUT" (구조적으로
                    불가능 -> Phase 1에 no-good).
      solution    : {"operations": {...}} 제출 dict, CUT이면 None.
      coords      : coords[i] -> (x, y) 정수 배치.
      orient      : orient[i] -> 선택된 orientation 인덱스.
      entry, exit_: Phase 2 repair 후 실제 타이밍.
      Z1          : 실제 총 지연, CUT이면 None.
      info        : 진단 정보.
    """
    status: str
    solution: Optional[dict]
    coords: dict
    orient: dict
    entry: list
    exit_: list
    Z1: Optional[float]
    info: Any = field(default_factory=dict)
