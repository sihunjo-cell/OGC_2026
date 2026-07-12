# scripts/record_baseline_fp.py
"""초기 디코드(BuildBayAssignment -> realize)의 fingerprint를 기록해
default-off 회귀 게이트(플레이북 §8-4)의 앵커로 쓴다. HEAD가 깨끗한 상태에서
1회 실행 후 fixture를 커밋한다."""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from Phase0 import preprocess                      # noqa: E402
from Phase1 import BuildBayAssignment              # noqa: E402
from Outer.realize import realize, _solution_ops_fingerprint  # noqa: E402

PROBS = ("prob_1", "prob_5")

def main():
    out = {}
    for name in PROBS:
        prob_info = json.load(open(ROOT / "train" / f"{name}.json", encoding="utf-8"))
        pre = preprocess(prob_info)
        p1 = BuildBayAssignment(prob_info, pre, None)
        s = realize(p1.bay, prob_info, pre, None)
        fp, ops = _solution_ops_fingerprint(s.solution)
        out[name] = {"fingerprint": fp, "op_count": ops, "objective": s.objective}
        print(name, out[name])
    fix = ROOT / "tests" / "fixtures"
    fix.mkdir(parents=True, exist_ok=True)
    json.dump(out, open(fix / "baseline_fp.json", "w", encoding="utf-8"), indent=1)

if __name__ == "__main__":
    main()
