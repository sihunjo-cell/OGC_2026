"""
Outer.portfolio -- ALNS 변형 4개를 같은 벽시계 시간 동안 병렬로 돌리고 최선을 취함
(best-of-N은 어떤 단일 멤버보다도 나쁘지 않다).

multiprocessing.Pool이 아니라 독립 OS 서브프로세스(Outer/worker.py)를 쓴다: 대회는
algorithm()을 __main__(테스터 러너)에 `if __name__ == "__main__"` 가드가 없는
서브프로세스에서 실행하는데, spawn Pool은 그 __main__을 워커마다 다시 import·실행해
포트폴리오가 중첩된다. 독립 프로세스는 각자 가드된 __main__을 가져 이 문제가 없다.

부모가 PRE를 한 번만 워밍·피클해 모든 워커가 뜨거운 NFP 캐시에서 시작하게 하여,
4중 cold NFP 빌드 중복과 그 경합을 피한다.
"""

from __future__ import annotations

import os
import sys
import json
import time
import pickle
import tempfile
import subprocess
import pathlib
from itertools import combinations

from Phase0 import preprocess
from Phase2 import Phase2Config
from .config import OuterConfig
from .alns import alns

_WORKER = str(pathlib.Path(__file__).resolve().parent / "worker.py")

# Selective NFP warm-up limits. Keep them small enough that parent-side warm-up
# stays cheap while still seeding the most likely early Phase 2 queries.
_WARM_CLIQUE_PAIR_CAP = 24
_WARM_GLOBAL_PAIR_CAP = 12
_WARM_CRANE_PAIR_CAP = 6
_WARM_NFP_CALL_CAP = 4000


def _block_area_priority(pre) -> list:
    return [max(aa) if aa else 0.0 for aa in pre.area]


def _pair_priority(pair, slack: list, area: list):
    i, j = pair
    return (min(slack[i], slack[j]),
            slack[i] + slack[j],
            -(area[i] + area[j]),
            -max(area[i], area[j]),
            i, j)


def _candidate_warm_pairs(pre, p1) -> list:
    """Choose a small set of likely-useful block pairs for NFP warm-up."""
    area = _block_area_priority(pre)
    slack = pre.slack

    clique_pairs = set()
    for cliques in getattr(p1, "cliques", []):
        for clique in cliques:
            if len(clique) < 2:
                continue
            for i, j in combinations(sorted(clique), 2):
                clique_pairs.add((i, j))

    ranked = sorted(clique_pairs, key=lambda p: _pair_priority(p, slack, area))
    chosen = ranked[:_WARM_CLIQUE_PAIR_CAP]
    chosen_set = set(chosen)

    # Hedge against later ALNS bay changes: add a few globally time-overlapping
    # pairs from the broader preprocess candidate set.
    fallback = sorted(pre.CO - chosen_set, key=lambda p: _pair_priority(p, slack, area))
    chosen.extend(fallback[:_WARM_GLOBAL_PAIR_CAP])
    return chosen


def _warm_pair_nfps(pre, i: int, j: int, warm_crane: bool, call_budget: int) -> int:
    """Warm canonical-order NFPs for a single pair within a fixed call budget."""
    used = 0
    for oi in range(len(pre.poly[i])):
        for oj in range(len(pre.poly[j])):
            li = len(pre.poly[i][oi])
            lj = len(pre.poly[j][oj])
            kmax = min(li, lj)
            for k in range(kmax):
                pre.nfp.same_level(i, j, oi, oj, k)
                used += 1
                if used >= call_budget:
                    return used
            if warm_crane and li and lj:
                for ki in range(min(li, lj)):
                    for kj in range(ki, lj):
                        pre.nfp.crane(i, j, oi, oj, ki, kj)
                        used += 1
                        if used >= call_budget:
                            return used
    return used


def _selective_warm_nfp(pre, p1) -> dict:
    """Warm a small, high-value subset of NFPs before worker launch."""
    pairs = _candidate_warm_pairs(pre, p1)
    used = 0
    warmed_pairs = 0
    for idx, (i, j) in enumerate(pairs):
        budget_left = _WARM_NFP_CALL_CAP - used
        if budget_left <= 0:
            break
        used += _warm_pair_nfps(pre, i, j,
                                warm_crane=(idx < _WARM_CRANE_PAIR_CAP),
                                call_budget=budget_left)
        warmed_pairs += 1
    return {"pairs": warmed_pairs, "calls": used, "cache_size": len(pre.nfp)}


def default_portfolio() -> list:
    """고분산 레버(jostle, forcing, 배치 순서) 스윕에서 한계 기여 방식으로 고른
    4개 멤버 포트폴리오. 멤버 0은 안전한 기준선이라 포트폴리오 최선이
    그보다 나빠지지 않는다."""
    return [
        # 2.6   forcing         order   xi   seed
        OuterConfig(xi=0.4, seed=0, phase2=Phase2Config(improve_mode="off",              forcing_mode="empty_bay")),                          # 하한 = 기준선(empty/area)
        OuterConfig(xi=0.3, seed=1, phase2=Phase2Config(improve_mode="jostle_2exchange", forcing_mode="empty_bay")),                          # jostle/area (주력, 10승)
        OuterConfig(xi=0.3, seed=4, phase2=Phase2Config(improve_mode="jostle_2exchange", forcing_mode="empty_bay",    order_mode="mst")),     # jostle + MST (5승)
        OuterConfig(xi=0.5, seed=5, phase2=Phase2Config(improve_mode="off",              forcing_mode="earliest_slot", order_mode="mst")),    # earliest + MST (10승)
    ]


def _warm_cache_legacy(prob_info: dict, pre, cfg: OuterConfig):
    """멤버 0의 초기해를 한 번 실행. 부수효과로 `pre`의 NFP 캐시를 채워 워커용
    피클이 가능하게 한다. 보장된 기준선 하한으로 (objective, solution) 반환,
    실패 시 (inf, None)."""
    from Phase1 import BuildBayAssignment
    from .realize import realize
    p1 = BuildBayAssignment(prob_info, pre, cfg.phase1)   # phase1=None이면 기본값
    s = realize(p1.bay, prob_info, pre, cfg.phase2)
    return (s.objective, s.solution) if s.feasible else (float("inf"), None)


def _warm_cache(prob_info: dict, pre, cfg: OuterConfig):
    """Run the seed solution once and use its overlap structure to pre-warm NFPs."""
    from Phase1 import BuildBayAssignment
    from .realize import realize
    p1 = BuildBayAssignment(prob_info, pre, cfg.phase1)
    _selective_warm_nfp(pre, p1)
    s = realize(p1.bay, prob_info, pre, cfg.phase2)
    return (s.objective, s.solution) if s.feasible else (float("inf"), None)


def _run_single(prob_info: dict, wall_budget: float, cfg: OuterConfig, pre=None):
    """멤버 하나를 프로세스 내에서 `wall_budget`초 동안 실행. 워밍된 `pre`가 주어지면
    재사용(NFP 캐시 재계산 회피). (obj, sol|None) 반환."""
    try:
        t0 = time.time()
        if pre is None:
            pre = preprocess(prob_info)
        alns_budget = max(1.0, wall_budget - (time.time() - t0) - 3.0)
        s, _ = alns(prob_info, pre, alns_budget, cfg)
        return (s.objective if s.feasible else float("inf"), s.solution)
    except Exception:
        return (float("inf"), None)


def optimize_portfolio(prob_info: dict, time_limit: float,
                       configs: list = None, n_workers: int = 4) -> dict:
    """기본 포트폴리오를 독립 서브프로세스로 돌려 최선의 실행가능해 operations dict를
    반환. 부모는 NFP 캐시를 한 번 워밍하고 PRE를 워커용으로 피클하며, 그 기준선을
    보장된 하한으로 유지한다. 커스텀 config, 단일 워커, 워밍/기동 실패 시에는
    멤버 0의 프로세스 내 실행으로 폴백."""
    use_default = configs is None
    configs = configs or default_portfolio()
    n = len(configs)
    t0 = time.time()

    # -- 1) 공유 NFP 캐시 워밍 + 보장된 기준선 확보 ----------------------------
    warm_obj, warm_sol, pre = float("inf"), None, None
    try:
        pre = preprocess(prob_info)
        # warm_obj, warm_sol = _warm_cache(prob_info, pre, configs[0]) # TODO 
        warm_obj, warm_sol = _warm_cache_legacy(prob_info, pre, configs[0])
    except Exception:
        pre = None

    # 프로세스 내 경로: 커스텀 config(워커가 재구성 못 함), 병렬 없음, 또는 워밍
    # 실패. 워밍된 pre로 순차 실행하고 기준선을 섞는다.
    if not use_default or n_workers <= 1 or n == 1 or pre is None:
        budget_left = max(1.0, time_limit - (time.time() - t0))
        results = [_run_single(prob_info, budget_left, c, pre) for c in configs]
        cands = [(o, s) for (o, s) in results if s is not None]
        if warm_sol is not None:
            cands.append((warm_obj, warm_sol))         # 기준선 하한
        return min(cands, key=lambda r: r[0])[1] if cands else warm_sol

    # 워밍이 예산을 (거의) 다 써서 워커가 realize 한 번도 못 끝냄 -- 대신
    # 보장된 기준선을 반환
    if warm_sol is not None and time_limit - (time.time() - t0) < 10.0:
        return warm_sol

    # -- 2) 워밍된 pre 피클 후 멤버마다 서브프로세스 하나씩 기동 ----------------
    tmpdir = tempfile.mkdtemp(prefix="ogc_pf_")
    prob_path = os.path.join(tmpdir, "prob.json")
    pre_path = os.path.join(tmpdir, "pre.pkl")
    with open(prob_path, "w", encoding="utf-8") as f:
        json.dump(prob_info, f)
    try:
        with open(pre_path, "wb") as f:
            pickle.dump(pre, f, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception:
        pre_path = ""     # 워커가 PRE를 직접 재계산

    # 워커 예산 = 워밍 후 남은 벽시계 - 부모의 수거 마진
    worker_wall = max(1.0, time_limit - (time.time() - t0) - _wrapup_margin(time_limit))

    procs = []           # (Popen, out_path) 목록
    try:
        for i in range(n):
            out_path = os.path.join(tmpdir, f"out_{i}.json")
            argv = [sys.executable, _WORKER, prob_path, str(i), str(worker_wall), out_path]
            if pre_path:
                argv.append(pre_path)
            p = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            procs.append((p, out_path))
    except Exception:
        for p, _ in procs:
            try: p.kill()
            except Exception: pass
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)
        return warm_sol if warm_sol is not None else \
            _run_single(prob_info, worker_wall, configs[0], pre)[1]

    # 수거: 각 워커는 worker_wall로 스스로 제한. 공동 마감까지 기다린 뒤
    # 남은 프로세스는 kill.
    deadline = time.time() + worker_wall + _wrapup_margin(time_limit)
    for p, _ in procs:
        remaining = max(1.0, deadline - time.time())
        try:
            p.wait(timeout=remaining)
        except Exception:
            try: p.kill()
            except Exception: pass

    # 워커들과 기준선 중 최선(기준선 목적값으로 초기화해 워커는 실제로 더
    # 나을 때만 이김)
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

    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)
    return best_sol


def _wrapup_margin(time_limit: float) -> float:
    """부모가 수거 전 워커 벽시계 예산을 넘겨 기다리는 초 -- 기동 지연 + 파일
    flush + 읽기를 커버. 상한을 두고 작게 유지."""
    return min(max(8.0, 0.03 * time_limit), 25.0)
