# tests/test_raster.py
import json
import math
import pathlib
import random
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np                                  # noqa: E402
from shapely.geometry import Point, Polygon         # noqa: E402

from Phase0 import preprocess                       # noqa: E402
from Phase2.raster import Raster                    # noqa: E402


def _load(name):
    prob_info = json.load(open(ROOT / "train" / f"{name}.json", encoding="utf-8"))
    return prob_info, preprocess(prob_info)


def test_mask_superset():
    """폴리곤 내부의 임의 점이 속한 셀은 반드시 마스크 1 (보수성의 핵심 방향)."""
    prob_info, pre = _load("prob_1")
    raster = Raster(prob_info, pre)
    rng = random.Random(1)
    checked = 0
    for i in range(5):
        for o in range(min(2, len(pre.poly[i]))):
            mask, mx0, my0 = raster.mask(i, o)
            for k, ring in enumerate(pre.poly[i][o]):
                poly = Polygon(ring)
                minx, miny, maxx, maxy = poly.bounds
                for _ in range(200):
                    px = rng.uniform(minx, maxx)
                    py = rng.uniform(miny, maxy)
                    if not poly.contains(Point(px, py)):
                        continue
                    r = int(math.floor(py)) - my0
                    c = int(math.floor(px)) - mx0
                    assert mask[k][r, c] == 1, (i, o, k, px, py)
                    checked += 1
    assert checked > 100


def test_stamp_exact_inverse():
    """add 후 remove하면 모든 점유 그리드가 0으로 복원 (카운트 점유의 계약)."""
    prob_info, pre = _load("prob_1")
    raster = Raster(prob_info, pre)
    i, o, j = 0, 0, 0
    (x_lo, x_hi), (y_lo, y_hi) = pre.IFP[i][o][j]
    assert x_lo <= x_hi and y_lo <= y_hi
    pos = (x_lo, y_lo)
    raster.add(j, i, o, pos)
    assert any(g.any() for g in raster.occ[j].values())
    assert raster.ver[j] == 1
    raster.remove(j, i, o, pos)
    assert all(not g.any() for g in raster.occ[j].values())
    assert raster.ver[j] == 2


if __name__ == "__main__":
    test_mask_superset()
    test_stamp_exact_inverse()
    print("OK")
