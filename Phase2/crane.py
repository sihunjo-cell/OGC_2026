"""Phase2.crane -- 2.4 크레인 실현가능성.

  crane_obstructed  -- 값싼 단일 위치 검사 (utils.check_entry/check_exit와 동일
    논리). driver의 earliest-slot forcing에서 사용.
  crane_feasibility -- utils.check_feasibility(평가 기준)로 최종 인증. feasibility와
    충돌 block id들을 반환해서 repair가 시간상 밀 수 있게 한다."""

from __future__ import annotations

import sys
import pathlib

from . import geometry_query as gq


# -- 단일 위치 크레인 검사 (2.4) ----------------------------------------

def crane_obstructed(i, o, pos, blockers, coords, orient, pre) -> bool:
    """(o, pos)의 블록 i가 `blockers` 중 하나에라도 크레인이 막히면 True
    (i의 layer k가 blocker의 layer j >= k와 겹침 -- j>=k sweep 규칙).
    entry/exit 두 순간 모두에 사용."""
    px, py = pos
    Ki = gq.num_layers(pre, i, o)
    for n in blockers:
        on = orient[n]
        dx = px - coords[n][0]
        dy = py - coords[n][1]
        Kn = gq.num_layers(pre, n, on)
        for k in range(Ki):
            for j2 in range(k, Kn):
                rings = gq.relative_nfp_crane(pre, moving=i, fixed=n,
                                              o_m=o, o_f=on, k_m=k, k_f=j2)
                if gq.point_in_rings(dx, dy, rings):
                    return True
    return False


# -- 최종 인증 (utils) ----------------------------------------------

def _load_utils():
    """대회 utils 모듈 import (서버에선 그냥 import, 안 되면 ogc2026/baseline
    개발 경로로 폴백)."""
    try:
        import utils
        return utils
    except ImportError:
        here = pathlib.Path(__file__).resolve().parent.parent
        for cand in (here / "ogc2026" / "baseline", here / "ogc2026" / "alg_tester"):
            if cand.is_dir() and str(cand) not in sys.path:
                sys.path.insert(0, str(cand))
        import utils
        return utils


def _blocks_in_violations(violations: list) -> list:
    """utils violation 문자열("... block <id> ...")에서 서로 다른 block id 추출."""
    ids = []
    seen = set()
    for v in violations:
        parts = v.split("block ")
        for p in parts[1:]:
            tok = p.split()[0].rstrip(":,)")
            try:
                bid = int(tok)
            except ValueError:
                continue
            if bid not in seen:
                seen.add(bid)
                ids.append(bid)
    return ids


def crane_feasibility(prob_info: dict, solution: dict) -> tuple:
    """utils.check_feasibility로 solution 인증.

    반환: (feasible: bool, conflict_block_ids: list[int], stage: int).
    stage는 실패한 단계 (2/3 = 크레인 entry/exit, 4 = 공간, 5 = 순서),
    feasible이면 5.
    """
    utils = _load_utils()
    res = utils.check_feasibility(prob_info, solution)
    if res["feasible"]:
        return True, [], res["stage"]
    return False, _blocks_in_violations(res["violations"]), res["stage"]
