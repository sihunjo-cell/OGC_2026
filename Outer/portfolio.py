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
from .floor import emergency_floor, LAST_FLOOR

_WORKER = str(pathlib.Path(__file__).resolve().parent / "worker.py")


def default_portfolio() -> list:
    """?대깽??援щ룞 dispatch 4-?뚯빱 ?ы듃?대━?? 觀3/F8쨌觀1/F24 媛?dyn-on/off ?섏뼱, min-wins.
    - dyn-on ?섏뼱(觀3쨌觀1): ?숈쟻 bay ?щ씪?고똿?쇰줈 ?쇱옟 臾몄젣 ?대떦(觀-?ㅼ뼇??. ?뺤껜 ?ъ떆??
      (觀3?뭩tall8, 觀1?뭩tall16)?쇰줈 ?μ삁?곗뿉??觀-吏??援ъ꽦????basin ?먯깋(?⑥삁?곗꽑 鍮꾪솢??.
    - dyn-off ?섏뼱(觀3쨌觀1): ?щ씪?고똿(?좏샇bay ?댄깉=Z3 ?먰빐)???대줈??怨?w3 ?좏삎??flooring.
    configs[0](觀3 dyn-on)??遺紐?warm 鍮뚮뱶濡?泥??몄쬆??+ ?⑥씪?뚯빱 fallback???대떦.
    ?뚯빱 ?쒕툕?꾨줈?몄뒪??BLAS 1?ㅻ젅???(?됯??쒕쾭 4肄붿뼱 cpulimit ?ㅻ줈? 諛⑹?)."""
    # ?F ?ы룊媛????issue/05): OGC_FRAGDELTA="w,queue_hi,horizon" ??dyn-on 2?뚯빱 ?곸슜.
    fd = {}
    _fd_env = os.environ.get("OGC_FRAGDELTA", "")
    if _fd_env:
        try:
            _w, _q, _h = (float(x) for x in _fd_env.split(","))
            fd = dict(dispatch_fragdelta=_w, fragdelta_queue_hi=int(_q),
                      fragdelta_horizon=int(_h))
        except Exception:
            fd = {}
    # hull-nestle k32/cap12 = dyn-on 湲곕낯(2026-07-16 ?밴꺽, 寃뚯씠?멤몺~??= issue/06).
    nes = dict(dispatch_nestle_k=32, dispatch_nestle_cap=12)
    return [
        OuterConfig(xi=0.3, seed=1, restart_stall=8,
                    phase2=Phase2Config(atc_kappa=3.0, dispatch_admit_fail_stop=8, **fd, **nes)),   # 觀3 dyn-on (warm, ?ъ떆??)
        OuterConfig(xi=0.3, seed=1, phase2=Phase2Config(atc_kappa=3.0, dispatch_admit_fail_stop=8,
                                                        dispatch_dynamic_bay=False)),                  # 觀3 dyn-off (32 floor)
        OuterConfig(xi=0.5, seed=5, restart_stall=16,
                    phase2=Phase2Config(atc_kappa=1.0, dispatch_admit_fail_stop=24,
                                        dispatch_beam=True, beam_depth=3, beam_width=8,
                                        beam_top_blocks=4, beam_top_anchors=4,
                                        beam_trigger_queue=8, beam_max_expansions=192,
                                        **fd, **nes)),  # κ1 dyn-on + beam
        OuterConfig(xi=0.5, seed=7, restart_stall=16,
                    phase2=Phase2Config(atc_kappa=1.0, dispatch_admit_fail_stop=24,
                                        dispatch_serial=True, serial_rule="large_critical",
                                        serial_top_blocks=6, serial_top_bays=3,
                                        serial_anchor_cap=8, serial_time_cap=32)),      # serial-SGS large-critical
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
        # warm 鍮뚮뱶 ?곹븳 max(30s, 0.35T): train 臾대컻?? ??뺤꽌留??뚯빱 ?덉궛 蹂댄샇.
        warm_deadline = deadline
        if deadline is not None:
            cap = max(30.0, 0.35 * float(time_limit))
            warm_deadline = min(deadline, time.perf_counter() + cap)
        warm_obj, warm_sol = _warm_cache(prob_info, pre, configs[0], deadline=warm_deadline)
    except Exception:
        pre = None

    # ?덈? 諛섑솚 蹂댁옣: ?대뒓 寃쎈줈?먯꽌 warm/?뚯빱媛 ???ㅽ뙣?대룄 None ???floor瑜??몃떎.
    # floor??min-鍮꾧탳?먯꽌 ?덈? 紐??닿린誘濡??뺤긽 寃쎈줈 寃곌낵??遺덈?.
    floor_sol = None
    if pre is not None:
        try:
            fs = emergency_floor(prob_info, pre)
            if fs.feasible:
                floor_sol = fs.solution
                LAST_FLOOR["sol"] = floor_sol
        except Exception:
            floor_sol = None

    def _best_fallback(cur):
        return cur if cur is not None else floor_sol

    if deadline is not None and time.perf_counter() >= deadline:
        return _best_fallback(warm_sol)

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
        return _best_fallback(min(cands, key=lambda r: r[0])[1] if cands else warm_sol)

    remaining = time_limit - (time.perf_counter() - t0)
    if deadline is not None:
        remaining = min(remaining, deadline - time.perf_counter())

    if warm_sol is not None and deadline is None and remaining < 10.0:
        return _best_fallback(warm_sol)
    if warm_sol is not None and deadline is not None and remaining <= 0.0:
        return _best_fallback(warm_sol)
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
        return _best_fallback(min(cands, key=lambda r: r[0])[1])
    if deadline is not None and time.perf_counter() >= deadline:
        return _best_fallback(warm_sol)

    tmpdir = tempfile.mkdtemp(prefix="ogc_pf_")
    prob_path = os.path.join(tmpdir, "prob.json")
    pre_path = os.path.join(tmpdir, "pre.pkl")
    with open(prob_path, "w", encoding="utf-8") as f:
        json.dump(prob_info, f)
    # 留덉뒪??罹먯떆(?섏떗 MB)???쇳겢 ?쒖쇅 -- ?뚯빱媛 ?먯껜 ?щ퉴???꾩넚鍮꾩슜 由ъ뒪???뚰뵾).
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
            # ?뚯빱 BLAS 1?ㅻ젅???(4肄붿뼱 cpulimit ?ㅻ줈? 諛⑹?, 寃곌낵 臾닿?).
            p = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 env={**os.environ,
                                      "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1",
                                      "OPENBLAS_NUM_THREADS": "1", "NUMEXPR_NUM_THREADS": "1"})
            procs.append((p, out_path))
    except Exception:
        for p, _ in procs:
            try:
                p.kill()
            except Exception:
                pass
        shutil.rmtree(tmpdir, ignore_errors=True)
        return _best_fallback(warm_sol if warm_sol is not None else _run_single(
            prob_info,
            worker_wall,
            configs[0],
            pre,
            deadline=deadline,
            deadline_s=deadline_s,
        )[1])

    wait_deadline = (
        deadline + _wrapup_margin(time_limit)
        if deadline is not None
        else time.perf_counter() + worker_wall + _wrapup_margin(time_limit)
    )
    for p, _ in procs:
        remaining = wait_deadline - time.perf_counter()
        try:
            if remaining > 0:
                p.wait(timeout=remaining)
            else:
                p.kill()               # wrapup 珥덇낵: ?뚯빱??+1s ??퉬 ?놁씠 利됱떆 ?뺣━
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
    return _best_fallback(best_sol)


def _wrapup_margin(time_limit: float) -> float:
    return min(max(8.0, 0.03 * time_limit), 25.0)
