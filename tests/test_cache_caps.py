"""Caps are pure memoization guards: results identical, size bounded."""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from Phase0.geometry_tables import NFPCache          # noqa: E402


def test_nfp_cache_clears_at_cap():
    poly = [[[[(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0)]]] for _ in range(3)]
    c = NFPCache(poly, mode="fast")
    c._cap = 2
    c.same_level(0, 1, 0, 0, 0)
    c.same_level(0, 2, 0, 0, 0)
    c.same_level(1, 2, 0, 0, 0)     # third distinct key -> exceeds cap -> clear
    assert len(c._cache) <= 2


if __name__ == "__main__":
    test_nfp_cache_clears_at_cap()
    print("OK")
