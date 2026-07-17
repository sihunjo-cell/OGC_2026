"""크레인 실현가능성: 단일 위치 검사(crane_obstructed) + 공식 utils 최종 인증."""

from __future__ import annotations

import sys
import pathlib

from . import geometry_query as gq


def crane_obstructed(i, o, pos, blockers, coords, orient, pre) -> bool:
    """블록 i가 blockers에 크레인 sweep(layer j>=k)으로 막히면 True."""
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


def crane_blocks_resident(candidate_i, candidate_o, candidate_pos,
                          resident_n, coords, orient, pre) -> bool:
    """후보 배치가 상주 resident_n의 반출 sweep을 막으면 True (역방향 검사)."""
    rx, ry = coords[resident_n]
    on = orient[resident_n]
    dx = rx - candidate_pos[0]
    dy = ry - candidate_pos[1]
    Kn = gq.num_layers(pre, resident_n, on)
    Ki = gq.num_layers(pre, candidate_i, candidate_o)
    for k in range(Kn):
        for j2 in range(k, Ki):
            rings = gq.relative_nfp_crane(pre, moving=resident_n, fixed=candidate_i,
                                          o_m=on, o_f=candidate_o, k_m=k, k_f=j2)
            if gq.point_in_rings(dx, dy, rings):
                return True
    return False


def _load_utils():
    """공식 utils import (실패 시 개발 경로 폴백)."""
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
    """violation 문자열에서 block id 추출."""
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
    """공식 인증 -> (feasible, 충돌 block ids, stage). stage 2/3=크레인, 4=공간, 5=순서."""
    utils = _load_utils()
    res = utils.check_feasibility(prob_info, solution)
    if res["feasible"]:
        return True, [], res["stage"]
    return False, _blocks_in_violations(res["violations"]), res["stage"]
