"""Phase2.improve -- 2.6 bay 내부 개선.

  * "off"              : 항등 (최소 경로 기본값).
  * "jostle_2exchange" : Jostle(각 블록을 충돌 없는 가장 조밀한 좌하단 자리로
                         밀기) 후 2-exchange(compaction이 좋아지면 co-resident
                         배치 맞바꾸기).

연산자는 공간 충돌 없음 / IFP 포함만 보장한다. 크레인 feasibility는 이후 driver의
repair 루프가 다시 인증. ImproveCtx가 추가 상태(타이밍, bay 배정, bay 멤버십)를 담는다."""

from __future__ import annotations

from dataclasses import dataclass

from .collision import _collision_free
from .candidates import candidate_positions, fits_ifp


@dataclass
class ImproveCtx:
    pre: object
    cfg: object
    prob_info: dict
    bay: list
    entry: list
    exit_: list
    bay_blocks: list


def _overlap(entry, exit_, a, b) -> bool:
    return entry[a] < exit_[b] and entry[b] < exit_[a]


def _residents(i, ids, ictx, exclude=()):
    return [k for k in ids
            if k != i and k not in exclude and _overlap(ictx.entry, ictx.exit_, i, k)]


def _jostle(coords, orient, ictx):
    """각 블록을 충돌 없는 후보 중 가장 아래, 그다음 가장 왼쪽으로 민다."""
    for _ in range(ictx.cfg.improve_rounds):
        moved = False
        for j in range(ictx.pre.n_bays):
            ids = [i for i in ictx.bay_blocks[j] if i in coords]
            for i in ids:
                o = orient[i]
                residents = _residents(i, ids, ictx)
                cur = coords[i]
                best_key = (cur[1], cur[0])
                best_pos = cur
                for pos in candidate_positions(i, o, j, residents, coords, orient,
                                               ictx.pre, ictx.cfg):
                    if (pos[1], pos[0]) >= best_key:
                        continue
                    if _collision_free(i, o, pos, residents, coords, orient, ictx.pre):
                        best_key = (pos[1], pos[0])
                        best_pos = pos
                if best_pos != cur:
                    coords[i] = best_pos
                    moved = True
        if not moved:
            break
    return coords, orient


def _top_y(i, o, pos, pre):
    return pos[1] + pre.bbox[i][o][3]


def _two_exchange(coords, orient, ictx):
    """co-resident 블록 둘의 (위치, orientation)을 맞바꾼다. 둘 다 충돌 없이
    bay 안에 남고, 맞바꿔서 두 블록의 상단 합이 낮아질 때만."""
    pre = ictx.pre
    for j in range(pre.n_bays):
        ids = [i for i in ictx.bay_blocks[j] if i in coords]
        for a in range(len(ids)):
            for b in range(a + 1, len(ids)):
                i, k = ids[a], ids[b]
                if not _overlap(ictx.entry, ictx.exit_, i, k):
                    continue
                oi, ok = orient[i], orient[k]
                pi, pk = coords[i], coords[k]
                # 맞바꾼 뒤: 각 블록은 자기 orientation 유지, 위치만 교환
                # (i는 k의 슬롯을, k는 i의 슬롯을 차지).
                if not (fits_ifp(pre, i, oi, j, pk) and fits_ifp(pre, k, ok, j, pi)):
                    continue
                res_i = _residents(i, ids, ictx, exclude=(k,))
                res_k = _residents(k, ids, ictx, exclude=(i,))
                # 임시 맞바꿈
                coords[i], coords[k] = pk, pi
                ok_i = _collision_free(i, oi, pk, res_i, coords, orient, pre)
                ok_k = _collision_free(k, ok, pi, res_k, coords, orient, pre)
                mutual = _collision_free(i, oi, pk, [k], coords, orient, pre)
                before = _top_y(i, oi, pi, pre) + _top_y(k, ok, pk, pre)
                after = _top_y(i, oi, pk, pre) + _top_y(k, ok, pi, pre)
                if ok_i and ok_k and mutual and after < before - 1e-9:
                    continue                              # 맞바꿈 수락 (이미 적용됨)
                coords[i], coords[k] = pi, pk             # 되돌림
    return coords, orient


def _jostle_2exchange(coords, orient, ictx):
    coords, orient = _jostle(coords, orient, ictx)
    coords, orient = _two_exchange(coords, orient, ictx)
    coords, orient = _jostle(coords, orient, ictx)
    return coords, orient


def _identity(coords, orient, ictx):
    return coords, orient


IMPROVE_MODES = {
    "off": _identity,
    "jostle_2exchange": _jostle_2exchange,
}


def improve_layout(coords, orient, ictx):
    """설정된 bay 내부 개선을 적용하고 (coords, orient) 반환."""
    return IMPROVE_MODES[ictx.cfg.improve_mode](coords, orient, ictx)
