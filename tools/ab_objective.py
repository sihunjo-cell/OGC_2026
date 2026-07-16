"""Deterministic decode objective snapshot for A/B during hardening edits."""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from Phase0 import preprocess                      # noqa: E402
from Phase1 import BuildBayAssignment              # noqa: E402
from Phase2 import Phase2Config                    # noqa: E402
from Outer.realize import realize                  # noqa: E402


def decode(name):
    prob = json.load(open(ROOT / "train" / f"{name}.json", encoding="utf-8"))
    pre = preprocess(prob)
    p1 = BuildBayAssignment(prob, pre, None)
    s = realize(p1.bay, prob, pre, Phase2Config())
    return len(prob["blocks"]), s


if __name__ == "__main__":
    names = sys.argv[1:] or ["prob_1", "prob_28", "prob_38", "prob_40"]
    for nm in names:
        n, s = decode(nm)
        print(f"{nm}: n={n} obj={s.objective:.1f} forced={s.forced} feas={s.feasible}")
