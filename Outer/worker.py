"""
Outer.worker -- run one portfolio member in a separate process.
"""

from __future__ import annotations

import json
import os
import pathlib
import pickle
import sys
import tempfile
import time

_WRITE_MARGIN = 3.0


def _load_pre(pre_path, prob_info):
    if pre_path:
        try:
            with open(pre_path, "rb") as f:
                return pickle.load(f)
        except Exception:
            pass
    from Phase0 import preprocess
    return preprocess(prob_info)


def run(prob_path: str, cfg_index: int, wall_budget: float, out_path: str,
        pre_path: str = None, deadline=None, deadline_s=None) -> None:
    t0 = time.perf_counter()
    here = pathlib.Path(__file__).resolve().parent.parent
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))

    result = {"obj": float("inf"), "solution": None}

    def _atomic_write(payload):
        # tmp + os.replace 원자 기록 (사망 시에도 마지막 best 보존)
        d = os.path.dirname(out_path) or "."
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".w", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            os.replace(tmp, out_path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def _on_best(obj, solution):
        _atomic_write({"obj": float(obj), "solution": solution})

    try:
        from Outer import alns
        from Outer.portfolio import default_portfolio

        with open(prob_path, "r", encoding="utf-8") as f:
            prob_info = json.load(f)

        cfg = default_portfolio(prob_info)[cfg_index]
        pre = _load_pre(pre_path, prob_info)
        alns_budget = None
        if deadline is None:
            alns_budget = max(1.0, wall_budget - (time.perf_counter() - t0) - _WRITE_MARGIN)
        # 부모 deadline을 자기 시계 예산으로 재상한
        deadline_eff = deadline
        if deadline is not None:
            deadline_eff = min(deadline,
                               time.perf_counter() + max(1.0, wall_budget - _WRITE_MARGIN))
        s, _ = alns(
            prob_info,
            pre,
            alns_budget,
            cfg,
            deadline=deadline_eff,
            deadline_s=deadline_s,
            on_best=_on_best,
        )
        result = {
            "obj": float(s.objective) if s.feasible else float("inf"),
            "solution": s.solution,
        }
    except Exception:
        import traceback
        result = {
            "obj": float("inf"),
            "solution": None,
            "error": traceback.format_exc()[-1000:],
        }

    _atomic_write(result)


if __name__ == "__main__":
    _pre = sys.argv[5] if len(sys.argv) > 5 and sys.argv[5] else None
    _deadline = float(sys.argv[6]) if len(sys.argv) > 6 and sys.argv[6] else None
    _deadline_s = float(sys.argv[7]) if len(sys.argv) > 7 and sys.argv[7] else None
    run(
        sys.argv[1],
        int(sys.argv[2]),
        float(sys.argv[3]),
        sys.argv[4],
        _pre,
        _deadline,
        _deadline_s,
    )
