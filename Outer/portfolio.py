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


def _swap_floors(prob_info) -> bool:
    """혼잡 중~대형 감지 (고-w3/소형은 False = floor 보존; 근거 = issue/07-cond)."""
    if prob_info is None:
        return False
    try:
        w = prob_info["weights"]
        if w["w3"] / max(w["w1"], 1e-9) >= 0.10:
            return False
        B = prob_info["blocks"]
        hz = max(b["due_date"] for b in B) - min(b["release_time"] for b in B)
        ba = sum(b2["width"] * b2["height"] for b2 in prob_info["bays"])
        au = 0.0
        for b in B:
            ring = b["shape"][0]["layers"][0]
            s = 0.0
            for i in range(len(ring)):
                x0, y0 = ring[i]
                x1, y1 = ring[(i + 1) % len(ring)]
                s += x0 * y1 - x1 * y0
            au += abs(s) * 0.5 * b["processing_time"]
        return len(B) * (au / max(ba * hz, 1e-9)) >= 50.0
    except Exception:
        return False


def default_portfolio(prob_info: dict = None) -> list:
    """4-워커 min-wins 포트폴리오: {κ3, κ1} x {dyn-on, dyn-off(floor)} -- 배선 근거는 메모리 원장."""
    # hull-nestle: κ3 = k32 유지(38 보호), κ1 = k64 재보정 (근거 = issue/06 후속 원장)
    nes = dict(dispatch_nestle_k=32, dispatch_nestle_cap=32,
               dispatch_nestle_flop_cap=2e10)
    nes1 = dict(dispatch_nestle_k=64, dispatch_nestle_cap=64,
                dispatch_nestle_flop_cap=2e11)
    # 형성기-게이트 ΔF: κ3 dyn-on 전용, κ1은 의도적 클린 (근거 = fgd 원장)
    fd = dict(dispatch_fragdelta=20.0, fragdelta_queue_hi=1, fragdelta_dens_hi=0.55)
    # orient-합동 순위: 혼잡(거인) 공격 워커 전용 (T600 은행 39 −7.84% 실측; slot0/비혼잡=off)
    oj = dict(dispatch_orient_joint=True)
    # κ·α 로터리(편입 07-22): 대형블록 우선(α=-0.5). 배포 A/B min-pool 순 −0.39M
    # (39 −6.69%·31 −3.20%·38 +1.47%, 26/27=α없는 전용슬롯 커버=불변). slot4(nm-K16 챔프=검증config).
    # 경쟁자 α-로터리(대형지연) 반전 채택. seed-robust(39 3seed·31 2seed·below-floor). 근거=ogc-timecool-basin.
    _alpha = dict(atc_alpha=-0.5)
    _congested = _swap_floors(prob_info)
    if _congested:
        # slot2 = κ1-nm32-oj + α (=nm32a). 매트릭스 14/14 재선정서 nm32→nm32a = seed-robust
        # −0.97%(mid-tier 38/39/26/30/33/36 제패, α를 K16 대신 K32 base에). 최대문제 회귀(18/20)는
        # min-pool서 flip/nm16 커버=무해. 배포 A/B 확증(39 −0.89%·26 −5.26% matrix-exact·20 무회귀).
        # 근거=ogc-portfolio-champion4-0723 / grid_widen_T720.
        slot2 = OuterConfig(xi=0.5, seed=5, restart_stall=16,
                            phase2=Phase2Config(atc_kappa=1.0, dispatch_admit_fail_stop=24,
                                                dispatch_nearmain_k=32,
                                                dispatch_nearmain_cap=16,
                                                dispatch_nearmain_dens_hi=0.55,
                                                **_alpha, **oj, **nes1))
        # κ1-off floor -> T4 = κ1-k64 + near-main(K16/cap8) = 38 봉인(K32는 38 +897k 회귀)
        slot4 = OuterConfig(xi=0.5, seed=5, restart_stall=16,
                            phase2=Phase2Config(atc_kappa=1.0, dispatch_admit_fail_stop=24,
                                                dispatch_nearmain_k=16,
                                                dispatch_nearmain_cap=8,
                                                dispatch_nearmain_dens_hi=0.55,
                                                **_alpha, **oj, **nes1))
        # W2 = κ1-k64 + 결정-플립 재시작(stall6) + inbay 순서-프로브 -- 27직격·28/31 순서축(−7.7/−2.3% 2seed)
        slot3 = OuterConfig(xi=0.5, seed=5, restart_stall=6, restart_flip=1, inbay_stall=4,
                            phase2=Phase2Config(atc_kappa=1.0, dispatch_admit_fail_stop=24,
                                                **oj, **nes1))
    else:
        slot2 = OuterConfig(xi=0.3, seed=1,
                            phase2=Phase2Config(atc_kappa=3.0, dispatch_admit_fail_stop=8,
                                                dispatch_dynamic_bay=False))               # κ3 floor
        slot4 = OuterConfig(xi=0.5, seed=5,
                            phase2=Phase2Config(atc_kappa=1.0, dispatch_admit_fail_stop=24,
                                                dispatch_dynamic_bay=False))               # κ1 floor
        slot3 = OuterConfig(xi=0.5, seed=5, restart_stall=16,
                            phase2=Phase2Config(atc_kappa=1.0, dispatch_admit_fail_stop=24,
                                                **nes1))                                    # κ1 dyn-on (k64)
    # slot0: 비혼잡 = κ3 dyn-on (warm, ΔF). 혼잡 = nm16(κ1-nm16-oj, seed7).
    # 근거(07-23 매트릭스 감사): 혼잡 14/14 config×seed 매트릭스서 k3fd는 0/14 승 = 죽은슬롯.
    # 혼잡 챔프셋 = {nm16a, nm16, flip, nm32} = 슬롯4개에 정확히 매칭. nm16 = 30·33·18·40 챔프
    # (p18 +20.9%). 배포 A/B{18,39} 확증(p18 −19.18% fidelity·p39 무회귀). 원장=ogc-portfolio-champion4-0723.
    if _congested:
        slot0 = OuterConfig(xi=0.5, seed=7, restart_stall=16,
                            phase2=Phase2Config(atc_kappa=1.0, dispatch_admit_fail_stop=24,
                                                dispatch_nearmain_k=16,
                                                dispatch_nearmain_cap=8,
                                                dispatch_nearmain_dens_hi=0.55,
                                                **oj, **nes1))
    else:
        slot0 = OuterConfig(xi=0.3, seed=1, restart_stall=8,
                            phase2=Phase2Config(atc_kappa=3.0, dispatch_admit_fail_stop=8, **fd, **nes))
    return [slot0, slot2, slot3, slot4]


def _warm_cache(prob_info: dict, pre, cfg: OuterConfig, deadline=None):
    from Phase1 import BuildBayAssignment
    from .realize import realize

    p1 = BuildBayAssignment(prob_info, pre, cfg.phase1, deadline=deadline)
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
    configs = configs or default_portfolio(prob_info)
    n = len(configs)
    t0 = time.perf_counter()

    warm_obj, warm_sol, pre = float("inf"), None, None
    try:
        pre = preprocess(prob_info)
        # warm 빌드 상한 max(30s, 0.35T)
        warm_deadline = deadline
        if deadline is not None:
            cap = max(30.0, 0.35 * float(time_limit))
            warm_deadline = min(deadline, time.perf_counter() + cap)
        warm_obj, warm_sol = _warm_cache(prob_info, pre, configs[0], deadline=warm_deadline)
    except Exception:
        pre = None

    # 절대 반환 보장: 전 실패 시 None 대신 floor (min에서 못 이김 = 정상 경로 불변)
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
    # 마스크 캐시는 피클 제외 (워커 자체 재빌드)
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
            # 워커 BLAS 1스레드 핀
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
    # subprocess 건강성 조기 감지: 워커가 결과를 못 내면(샌드박스 IPC 실패) in-process ALNS 폴백
    # (warm-only near-last 방지). 정상 subprocess면 첫 best가 곧 나와 무발동 = 기존 경로 동일.
    if worker_wall >= 90.0:
        _hb = time.perf_counter() + 60.0
        if deadline is not None:
            _hb = min(_hb, deadline - 5.0)
        _saw = False
        while time.perf_counter() < _hb:
            if any(os.path.exists(op) and os.path.getsize(op) > 2 for _, op in procs):
                _saw = True
                break
            time.sleep(1.0)
        if not _saw:
            for p, _ in procs:
                try:
                    p.kill()
                except Exception:
                    pass
            shutil.rmtree(tmpdir, ignore_errors=True)
            _rem = ((deadline - time.perf_counter()) if deadline is not None
                    else worker_wall - 60.0)
            if _rem > 3.0:
                _o, _s = _run_single(prob_info, _rem, configs[0], pre,
                                     deadline=deadline, deadline_s=deadline_s)
                _cands = [(warm_obj, warm_sol)]
                if _s is not None:
                    _cands.append((_o, _s))
                return _best_fallback(min(_cands, key=lambda r: r[0])[1])
            return _best_fallback(warm_sol)
    for p, _ in procs:
        remaining = wait_deadline - time.perf_counter()
        try:
            if remaining > 0:
                p.wait(timeout=remaining)
            else:
                p.kill()               # wrapup 초과 즉시 정리
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
