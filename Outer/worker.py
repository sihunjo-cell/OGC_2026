"""
Outer.worker -- 포트폴리오 멤버 하나를 별도 OS 프로세스로 실행. optimize_portfolio가
기동:

    python <submission>/Outer/worker.py <prob_json> <cfg_index> <budget> <out_json> [pre_pickle]

독립 프로세스(multiprocessing.Pool 아님)라 테스터의 가드 없는 __main__을 재실행하지
않는다. 부모가 워밍한 PRE 피클(5번째 인자)을 로드해 첫 realize가 cold NFP 빌드 대신
캐시 히트가 되게 하고, default_portfolio()[cfg_index]로 config를 재구성한 뒤 벽시계
예산 안에서 ALNS를 돌리고(PRE 로드 시간 포함), {"obj", "solution"}을 <out_json>에 쓴다.
"""

from __future__ import annotations

import sys
import json
import time
import pickle
import pathlib

_WRITE_MARGIN = 3.0   # 마지막 realize + JSON 쓰기용 예비 시간(초)


def _load_pre(pre_path, prob_info):
    """부모가 워밍한 PRE를 피클에서 로드, 실패하면 직접 계산."""
    if pre_path:
        try:
            with open(pre_path, "rb") as f:
                return pickle.load(f)
        except Exception:
            pass
    from Phase0 import preprocess
    return preprocess(prob_info)


def run(prob_path: str, cfg_index: int, wall_budget: float, out_path: str,
        pre_path: str = None) -> None:
    t0 = time.time()
    # cwd와 무관하게 제출 패키지(Phase0..Outer)를 import할 수 있게: 이 파일은
    # <submission>/Outer/worker.py에 있음.
    here = pathlib.Path(__file__).resolve().parent.parent
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))

    result = {"obj": float("inf"), "solution": None}
    try:
        from Outer import alns
        from Outer.portfolio import default_portfolio

        with open(prob_path, "r", encoding="utf-8") as f:
            prob_info = json.load(f)

        cfg = default_portfolio()[cfg_index]
        pre = _load_pre(pre_path, prob_info)          # 있으면 워밍된 캐시
        alns_budget = max(1.0, wall_budget - (time.time() - t0) - _WRITE_MARGIN)
        s, _ = alns(prob_info, pre, alns_budget, cfg)
        result = {
            "obj": float(s.objective) if s.feasible else float("inf"),
            "solution": s.solution,
        }
    except Exception:
        import traceback
        result = {"obj": float("inf"), "solution": None,
                  "error": traceback.format_exc()[-1000:]}

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f)


if __name__ == "__main__":
    _pre = sys.argv[5] if len(sys.argv) > 5 else None
    run(sys.argv[1], int(sys.argv[2]), float(sys.argv[3]), sys.argv[4], _pre)
