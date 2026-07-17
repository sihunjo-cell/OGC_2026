"""배치와 무관한 스칼라 상수 계산."""

from __future__ import annotations


def precompute_constants(prob_info: dict) -> dict:
    """u, Smax, EST, slack 상수 계산 (0-based id 인덱스)."""
    bays = prob_info["bays"]
    blocks = prob_info["blocks"]
    m = len(bays)

    areas = [b["width"] * b["height"] for b in bays]
    Abar = sum(areas) / m
    u = [Abar / a for a in areas]

    Smax, EST, slack = [], [], []
    for blk in blocks:
        R = blk["release_time"]
        D = blk["due_date"]
        P = blk["processing_time"]
        Smax.append(max(blk["bay_preferences"]))
        EST.append(R)
        slack.append(D - R - P)

    return {"u": u, "Smax": Smax, "EST": EST, "slack": slack}
