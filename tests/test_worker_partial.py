"""A killed worker's last best must survive via atomic partial writes."""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from Phase0 import preprocess                       # noqa: E402
from Outer.alns import alns                          # noqa: E402


def test_on_best_called_and_monotone():
    prob = json.load(open(ROOT / "train" / "prob_28.json", encoding="utf-8"))
    pre = preprocess(prob)
    seen = []
    from Outer.portfolio import default_portfolio
    cfg = default_portfolio()[0]
    # short budget: still yields >=1 best (the initial realize).
    alns(prob, pre, budget_s=8.0, cfg=cfg,
         on_best=lambda o, s: seen.append((o, s)))
    assert seen, "on_best never fired (should fire at least for initial best)"
    objs = [o for o, _ in seen]
    assert objs == sorted(objs, reverse=True), objs  # non-increasing objective
    assert seen[-1][1] is not None and "operations" in seen[-1][1]


if __name__ == "__main__":
    test_on_best_called_and_monotone()
    print("OK")
