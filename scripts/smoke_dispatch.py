# scripts/smoke_dispatch.py
"""OGC_PORTFOLIO=dispatch로 algorithm(prob, 30) 스모크. rescue에서 T=30
TIMEOUT(kill35s)이던 prob_20이 예산 안에 feasible 해를 내는지 확인."""
import json
import os
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["OGC_PORTFOLIO"] = "dispatch"

from myalgorithm import algorithm                  # noqa: E402
from Phase2.crane import _load_utils               # noqa: E402

def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "prob_20"
    T = float(sys.argv[2]) if len(sys.argv) > 2 else 30.0
    prob_info = json.load(open(ROOT / "train" / f"{name}.json", encoding="utf-8"))
    t0 = time.perf_counter()
    sol = algorithm(prob_info, T)
    wall = time.perf_counter() - t0
    chk = _load_utils().check_feasibility(prob_info, sol)
    print(f"{name} T={T}: wall={wall:.1f}s feasible={chk['feasible']} "
          f"obj={chk.get('objective')}")
    assert wall < T * 1.15, f"budget overrun: {wall:.1f}s"
    assert chk["feasible"]

if __name__ == "__main__":
    main()
