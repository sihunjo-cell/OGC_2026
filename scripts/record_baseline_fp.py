# scripts/record_baseline_fp.py
"""기본(dispatch) 디코드의 결정론적 특성(feasible/objective/forced/n)을 기록해
회귀 게이트(tests/test_regression_baseline.py)의 앵커로 쓴다. 코드 변경 후
디코드 특성이 의도대로 바뀌었으면 재실행해 fixture를 갱신·커밋한다."""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from Phase0 import preprocess                      # noqa: E402
from Phase1 import BuildBayAssignment              # noqa: E402
from Phase2 import Phase2Config                    # noqa: E402
from Outer.realize import realize                  # noqa: E402

PROBS = ("prob_1", "prob_5")


def main():
    out = {}
    for name in PROBS:
        prob_info = json.load(open(ROOT / "train" / f"{name}.json", encoding="utf-8"))
        n = len(prob_info["blocks"])
        pre = preprocess(prob_info)
        p1 = BuildBayAssignment(prob_info, pre, None)
        s = realize(p1.bay, prob_info, pre, Phase2Config())
        out[name] = {"n": n, "objective": s.objective, "forced": s.forced,
                     "feasible": s.feasible}
        print(name, out[name])
    fix = ROOT / "tests" / "fixtures"
    fix.mkdir(parents=True, exist_ok=True)
    json.dump(out, open(fix / "baseline_fp.json", "w", encoding="utf-8"), indent=1)


if __name__ == "__main__":
    main()
