"""Phase0.constants -- 0.1단계: prob_info에서 배치와 무관한 스칼라 상수 계산."""

from __future__ import annotations


def precompute_constants(prob_info: dict) -> dict:
    """스칼라 상수(0.1). 모든 리스트는 0-based block / bay id로 인덱싱.

    반환 dict: u(bay 면적 가중치 Abar/(W*H)), Abar(평균 bay 면적),
    Smax(최대 bay 선호), EST(release), LST0(due - proc), slack.
    """
    bays = prob_info["bays"]
    blocks = prob_info["blocks"]
    m = len(bays)

    areas = [b["width"] * b["height"] for b in bays]
    Abar = sum(areas) / m
    u = [Abar / a for a in areas]

    Smax, EST, LST0, slack = [], [], [], []
    for blk in blocks:
        R = blk["release_time"]
        D = blk["due_date"]
        P = blk["processing_time"]
        Smax.append(max(blk["bay_preferences"]))
        EST.append(R)
        LST0.append(D - P)
        slack.append(D - R - P)

    return {"u": u, "Abar": Abar, "Smax": Smax, "EST": EST, "LST0": LST0, "slack": slack}
