"""Phase2.candidates -- 2.2: bay j에서 블록 i(o)의 정수 후보 위치. IFP 좌하단
코너(BLF seed) + IFP 안에 떨어지는 모든 resident-NFP 경계 꼭짓점. 여전히 겹치는
건 collision oracle이 걸러낸다."""

from __future__ import annotations

from . import geometry_query as gq


def _ifp_box(pre, i: int, o: int, j: int):
    (x_lo, x_hi), (y_lo, y_hi) = pre.IFP[i][o][j]
    return x_lo, x_hi, y_lo, y_hi


def _in_ifp(x, y, box) -> bool:
    x_lo, x_hi, y_lo, y_hi = box
    return x_lo <= x <= x_hi and y_lo <= y <= y_hi


def fits_ifp(pre, i: int, o: int, j: int, pos) -> bool:
    """정수 위치 pos가 (o, j)에 대한 블록 i의 IFP 박스 안이면 True."""
    return _in_ifp(pos[0], pos[1], _ifp_box(pre, i, o, j))


def _resident_nfp_vertices(i, o, residents, coords, orient, pre):
    """각 resident-NFP 경계 꼭짓점을 i의 배치 좌표계로 옮겨서 yield
    (pos_i = pos_n + 경계 꼭짓점). ring 꼭짓점마다 정확히 한 번씩."""
    for n in residents:
        on = orient[n]
        pnx, pny = coords[n]
        kmax = min(gq.num_layers(pre, i, o), gq.num_layers(pre, n, on))
        for k in range(kmax):
            rings = gq.relative_nfp(pre, moving=i, fixed=n, o_m=o, o_f=on, k=k)
            for r in rings:
                for (vx, vy) in r["ext"]:
                    yield (pnx + vx, pny + vy)


def _vertex_candidates(i, o, j, residents, coords, orient, pre):
    box = _ifp_box(pre, i, o, j)
    x_lo, x_hi, y_lo, y_hi = box
    if x_lo > x_hi or y_lo > y_hi:
        return set()
    cands = {(x_lo, y_lo)}                                # IFP 좌하단 코너 (BLF seed)
    # 속도 위해 _resident_nfp_vertices + _in_ifp 인라인 (동일한 결과 스트림).
    rnfp = gq.relative_nfp
    nlay = gq.num_layers
    _round = round
    _int = int
    nli = nlay(pre, i, o)                                 # resident 루프에서 불변
    for n in residents:
        on = orient[n]
        pnx, pny = coords[n]
        nln = nlay(pre, n, on)
        kmax = nli if nli < nln else nln                 # == min(nli, nln)
        for k in range(kmax):
            for r in rnfp(pre, moving=i, fixed=n, o_m=o, o_f=on, k=k):
                for (vx, vy) in r["ext"]:
                    x = _int(_round(pnx + vx))
                    y = _int(_round(pny + vy))
                    if x_lo <= x <= x_hi and y_lo <= y <= y_hi:
                        cands.add((x, y))
    return cands


def candidate_positions(i, o, j, residents, coords, orient, pre, cfg) -> list:
    """bay j에서 블록 i(o)의 정렬된 정수 후보 위치 (BLF 방식)."""
    return sorted(_vertex_candidates(i, o, j, residents, coords, orient, pre))
