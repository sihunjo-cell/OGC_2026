"""
Outer 스모크 테스트 / CLI.

    python -m Outer <instance> [--time S] [--xi F] [--seed N]

주어진 벽시계 시간 동안 Phase 0 -> ALNS(내부적으로 Phase 1 + Phase 2 사용)를 돌린 뒤,
s_best를 utils로 검증하고 목적함수 궤적을 출력.
"""

from __future__ import annotations

import argparse
import json
import time

from Phase0 import preprocess
from .config import OuterConfig
from .alns import alns
from Phase2.crane import _load_utils


def main() -> None:
    ap = argparse.ArgumentParser(description="OUTER ALNS smoke test")
    ap.add_argument("instance")
    ap.add_argument("--time", type=float, default=30.0, help="wall-clock budget (s)")
    ap.add_argument("--xi", type=float, default=0.4)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    prob_info = json.load(open(args.instance))
    pre = preprocess(prob_info)
    cfg = OuterConfig(xi=args.xi, seed=args.seed)

    log = None if args.quiet else (lambda m: print("  " + m))
    t0 = time.time()
    s_best, stats = alns(prob_info, pre, args.time, cfg, log=log)
    dt = time.time() - t0

    chk = _load_utils().check_feasibility(prob_info, s_best.solution)
    print(f"instance : {prob_info.get('name','?')}  ({pre.n_blocks} blocks / {pre.n_bays} bays)")
    print(f"budget   : {args.time:.0f}s   ran {dt:.1f}s   iters={stats['iters']}  "
          f"accepted={stats['accepted']}  improved={stats['improved']}")
    print(f"f0 -> best : {stats['f0']:.0f} -> {stats['f_best']:.0f}  "
          f"({100*(stats['f0']-stats['f_best'])/max(1,stats['f0']):.1f}% better)")
    if chk["feasible"]:
        print(f"utils    : feasible  OBJECTIVE={chk['objective']:.0f}  "
              f"(Z1={chk['obj1']:.0f} Z2={chk['obj2']:.0f} Z3={chk['obj3']:.0f})")
        print(f"match    : ALNS f == utils obj: {abs(s_best.objective - chk['objective']) < 1e-6}")
    else:
        print(f"utils    : INFEASIBLE stage={chk['stage']}")


if __name__ == "__main__":
    main()
