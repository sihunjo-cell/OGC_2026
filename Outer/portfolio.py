"""
Outer.portfolio -- run ALNS portfolio members in parallel and keep the best result.
"""

from __future__ import annotations

import json
import os
import pathlib
import pickle
import shutil
import subprocess
import sys
import tempfile
import time

from Phase0 import preprocess
from Phase2 import Phase2Config
from .alns import alns
from .config import OuterConfig

_WORKER = str(pathlib.Path(__file__).resolve().parent / "worker.py")


def default_portfolio() -> list:
    """이벤트 구동 dispatch 4-워커 포트폴리오 (A/B로 전 인스턴스 최고 성능 확정).
    선택 = κ3/F8·κ1/F24 각 dyn-on/off 페어 = 12열(4config×{off,on,guard}) 60s 전수 C(12,4)
    + LOO-CV로 확정한 균형해(-12.8%, 구조적 회귀 prob_34 +4.9%뿐).
    - dyn-on 페어(κ3·κ1): 혼잡 문제를 min-wins로 승리(재라우팅=Z1 대폭↓, κ-다양성).
    - dyn-off 페어(κ3·κ1): 재라우팅(선호bay 이탈=Z3 손해)이 해로운 고-w3 유형(w3/w1≥0.16,
      전40 중 3문제: 32→κ3-off, 37/25→κ1-off)을 base 수준으로 flooring하는 2중 보험.
    configs[0](κ3 dyn-on)이 부모 warm 빌드로 첫 인증해 + 단일워커 fallback을 담당.
    mask/scan_incremental 기본 on(비트동일)."""
    return [
        OuterConfig(xi=0.3, seed=1, phase2=Phase2Config(atc_kappa=3.0, dispatch_admit_fail_stop=8)),  # κ3 dyn-on (warm)
        OuterConfig(xi=0.3, seed=1, phase2=Phase2Config(atc_kappa=3.0, dispatch_admit_fail_stop=8,
                                                        dispatch_dynamic_bay=False)),                  # κ3 dyn-off (32 floor)
        OuterConfig(xi=0.5, seed=5, phase2=Phase2Config(atc_kappa=1.0, dispatch_admit_fail_stop=24)), # κ1 dyn-on (혼잡 최강)
        OuterConfig(xi=0.5, seed=5, phase2=Phase2Config(atc_kappa=1.0, dispatch_admit_fail_stop=24,
                                                        dispatch_dynamic_bay=False)),                  # κ1 dyn-off (37/25 floor)
    ]


def _warm_cache(prob_info: dict, pre, cfg: OuterConfig, deadline=None):
    from Phase1 import BuildBayAssignment
    from .realize import realize

    p1 = BuildBayAssignment(prob_info, pre, cfg.phase1)
    s = realize(p1.bay, prob_info, pre, cfg.phase2, deadline=deadline)
    return (s.objective, s.solution) if s.feasible else (float("inf"), None)


def _run_single(prob_info: dict, wall_budget: float, cfg: OuterConfig, pre=None,
                deadline=None, deadline_s=None):
    try:
        t0 = time.perf_counter()
        if pre is None:
            pre = preprocess(prob_info)

        alns_budget = None
        if deadline is None:
            alns_budget = max(1.0, wall_budget - (time.perf_counter() - t0) - 3.0)

        s, _ = alns(
            prob_info,
            pre,
            alns_budget,
            cfg,
            deadline=deadline,
            deadline_s=deadline_s,
        )
        return (s.objective if s.feasible else float("inf"), s.solution)
    except Exception:
        return (float("inf"), None)


def optimize_portfolio(prob_info: dict, time_limit: float,
                       configs: list = None, n_workers: int = 4,
                       deadline=None, deadline_s=None) -> dict:
    use_default = configs is None
    configs = configs or default_portfolio()
    n = len(configs)
    t0 = time.perf_counter()

    warm_obj, warm_sol, pre = float("inf"), None, None
    try:
        pre = preprocess(prob_info)
        warm_obj, warm_sol = _warm_cache(prob_info, pre, configs[0], deadline=deadline)
    except Exception:
        pre = None

    if deadline is not None and time.perf_counter() >= deadline:
        return warm_sol

    if not use_default or n_workers <= 1 or n == 1 or pre is None:
        budget_left = max(1.0, time_limit - (time.perf_counter() - t0))
        results = [
            _run_single(
                prob_info,
                budget_left,
                c,
                pre,
                deadline=deadline,
                deadline_s=deadline_s,
            )
            for c in configs
        ]
        cands = [(o, s) for (o, s) in results if s is not None]
        if warm_sol is not None:
            cands.append((warm_obj, warm_sol))
        return min(cands, key=lambda r: r[0])[1] if cands else warm_sol

    remaining = time_limit - (time.perf_counter() - t0)
    if deadline is not None:
        remaining = min(remaining, deadline - time.perf_counter())

    if warm_sol is not None and deadline is None and remaining < 10.0:
        return warm_sol
    if warm_sol is not None and deadline is not None and remaining <= 0.0:
        return warm_sol
    if warm_sol is not None and deadline is not None and remaining < 10.0:
        obj, sol = _run_single(
            prob_info,
            remaining,
            configs[0],
            pre,
            deadline=deadline,
            deadline_s=deadline_s,
        )
        cands = [(warm_obj, warm_sol)]
        if sol is not None:
            cands.append((obj, sol))
        return min(cands, key=lambda r: r[0])[1]
    if deadline is not None and time.perf_counter() >= deadline:
        return warm_sol

    tmpdir = tempfile.mkdtemp(prefix="ogc_pf_")
    prob_path = os.path.join(tmpdir, "prob.json")
    pre_path = os.path.join(tmpdir, "pre.pkl")
    with open(prob_path, "w", encoding="utf-8") as f:
        json.dump(prob_info, f)
    # warm 빌드가 pre에 붙인 마스크 캐시(수십 MB)는 피클에서 제외 -- 워커는 첫
    # realize에서 자체 재빌드 후 재사용하므로(측정도 그 기준) 크로스-프로세스 전송은
    # 불필요한 보너스일 뿐이고, 부하 시 피클/전송 비용이 예산을 갉는 리스크만 준다.
    try:
        delattr(pre, "_raster_mask_cache")
    except AttributeError:
        pass
    try:
        with open(pre_path, "wb") as f:
            pickle.dump(pre, f, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception:
        pre_path = ""

    if deadline is not None:
        worker_wall = max(1.0, deadline - time.perf_counter())
    else:
        worker_wall = max(1.0, time_limit - (time.perf_counter() - t0) - _wrapup_margin(time_limit))

    procs = []
    try:
        for i in range(n):
            out_path = os.path.join(tmpdir, f"out_{i}.json")
            argv = [
                sys.executable,
                _WORKER,
                prob_path,
                str(i),
                str(worker_wall),
                out_path,
                pre_path or "",
                "" if deadline is None else str(deadline),
                "" if deadline_s is None else str(deadline_s),
            ]
            p = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            procs.append((p, out_path))
    except Exception:
        for p, _ in procs:
            try:
                p.kill()
            except Exception:
                pass
        shutil.rmtree(tmpdir, ignore_errors=True)
        return warm_sol if warm_sol is not None else _run_single(
            prob_info,
            worker_wall,
            configs[0],
            pre,
            deadline=deadline,
            deadline_s=deadline_s,
        )[1]

    wait_deadline = (
        deadline + _wrapup_margin(time_limit)
        if deadline is not None
        else time.perf_counter() + worker_wall + _wrapup_margin(time_limit)
    )
    for p, _ in procs:
        remaining = max(1.0, wait_deadline - time.perf_counter())
        try:
            p.wait(timeout=remaining)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass

    best_obj = warm_obj
    best_sol = warm_sol
    for _, out_path in procs:
        try:
            with open(out_path, "r", encoding="utf-8") as f:
                rec = json.load(f)
            if rec.get("solution") is not None and rec.get("obj", float("inf")) < best_obj:
                best_obj, best_sol = rec["obj"], rec["solution"]
        except Exception:
            continue

    shutil.rmtree(tmpdir, ignore_errors=True)
    return best_sol


def _wrapup_margin(time_limit: float) -> float:
    return min(max(8.0, 0.03 * time_limit), 25.0)
