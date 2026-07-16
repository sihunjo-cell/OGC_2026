import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from Phase0 import preprocess                       # noqa: E402
from Phase2.crane import _load_utils                # noqa: E402
from Outer.floor import emergency_floor             # noqa: E402


def test_floor_is_feasible_and_certified():
    utils = _load_utils()
    for name in ("prob_1", "prob_28", "prob_38"):
        prob = json.load(open(ROOT / "train" / f"{name}.json", encoding="utf-8"))
        pre = preprocess(prob)
        s = emergency_floor(prob, pre)
        assert s.feasible, name
        assert len(s.coords) == len(prob["blocks"]), name
        chk = utils.check_feasibility(prob, s.solution)
        assert chk["feasible"], (name, chk.get("violations", [])[:2])
        assert s.objective < float("inf")


if __name__ == "__main__":
    test_floor_is_feasible_and_certified()
    print("OK")
