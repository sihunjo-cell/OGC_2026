"""_force per-bay snapshot must stay checker-feasible and complete under a
heavy force tail (all blocks assigned to one bay). Characterization test: it
must keep passing after the O(1)-snapshot refactor (bit-identical decode)."""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from Phase0 import preprocess                       # noqa: E402
from Phase2 import Phase2Config                     # noqa: E402
from Phase2.dispatch import dispatch_construct      # noqa: E402
from Phase2.driver import build_solution            # noqa: E402
from Phase1.timing import init_timing               # noqa: E402
from Phase2.crane import _load_utils                # noqa: E402


def test_force_tail_complete_and_feasible():
    prob = json.load(open(ROOT / "train" / "prob_38.json", encoding="utf-8"))
    pre = preprocess(prob)
    n = len(prob["blocks"])
    bay = [0] * n                                    # overload bay 0 -> heavy force tail
    p1 = init_timing(bay, prob, pre)
    coords, orient, entry, exit_, forced, rbay = dispatch_construct(
        prob, p1, pre, Phase2Config(dispatch_dynamic_bay=False))
    assert len(coords) == n                          # every block placed
    sol = build_solution(coords, orient, entry, exit_, rbay, range(n))
    chk = _load_utils().check_feasibility(prob, sol)
    assert chk["feasible"], chk.get("violations", [])[:2]


def test_past_deadline_force_is_fast_and_feasible():
    """A block-heavy decode past deadline must complete via O(1) tail-pointer
    force (not the O(k^2) empty-window search) and stay checker-feasible."""
    import time
    prob = json.load(open(ROOT / "train" / "prob_38.json", encoding="utf-8"))
    pre = preprocess(prob)
    n = len(prob["blocks"])
    bay = [0] * n
    p1 = init_timing(bay, prob, pre)
    dl = time.perf_counter() - 1.0                   # already past: force whole queue
    t = time.perf_counter()
    coords, orient, entry, exit_, forced, rbay = dispatch_construct(
        prob, p1, pre, Phase2Config(dispatch_dynamic_bay=False), deadline=dl)
    dt = time.perf_counter() - t
    assert len(coords) == n
    assert dt < 5.0, f"tail-pointer force too slow: {dt:.1f}s"
    sol = build_solution(coords, orient, entry, exit_, rbay, range(n))
    chk = _load_utils().check_feasibility(prob, sol)
    assert chk["feasible"], chk.get("violations", [])[:2]


if __name__ == "__main__":
    test_force_tail_complete_and_feasible()
    test_past_deadline_force_is_fast_and_feasible()
    print("OK")
