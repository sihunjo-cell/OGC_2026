# tests/test_regression_baseline.py
"""default 설정 초기 디코드가 fixture와 byte-identical한지 검증.
신규 코드가 default-off를 지키는지 확인하는 게이트 — 모든 태스크 뒤에 재실행."""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from Phase0 import preprocess                      # noqa: E402
from Phase1 import BuildBayAssignment              # noqa: E402
from Outer.realize import realize, _solution_ops_fingerprint  # noqa: E402


def test_initial_decode_fingerprint_unchanged():
    fixture = json.load(open(ROOT / "tests" / "fixtures" / "baseline_fp.json", encoding="utf-8"))
    for name, want in fixture.items():
        prob_info = json.load(open(ROOT / "train" / f"{name}.json", encoding="utf-8"))
        pre = preprocess(prob_info)
        p1 = BuildBayAssignment(prob_info, pre, None)
        s = realize(p1.bay, prob_info, pre, None)
        fp, ops = _solution_ops_fingerprint(s.solution)
        assert fp == want["fingerprint"], (name, fp, want["fingerprint"])
        assert ops == want["op_count"], (name, ops, want["op_count"])


if __name__ == "__main__":
    test_initial_decode_fingerprint_unchanged()
    print("OK")
