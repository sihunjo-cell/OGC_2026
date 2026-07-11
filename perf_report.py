#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""
perf_report.py -- OGC 2026 솔버 성능/시간제한 분석 하네스 (단일 파일, 크래시 내성).

전제(problem-statement p.9~10): timelimit은 문제별 상이(수분~30분)하며
algorithm(prob, timelimit) 인자로만 주어지고, 시간초과=crash=infeasible=-1점.

모드:
  --mode anytime (기본) : 문제당 단일 ALNS(member0)를 --horizon 초 1회 관측,
      incumbent 개선을 타임스탬프로 기록 -> 모든 가상 timelimit의 품질을
      후처리로 읽음(재실행 불필요).
  --mode real : 문제 x timelimit마다 새 프로세스에서 algorithm(prob, T) 실행,
      T*(1+grace)에 하드킬, -1 규칙으로 채점. (alg_tester는 시간을 강제하지
      않으므로 이게 유일한 컴플라이언스 검증 수단.)
  --mode both : anytime 후 real.

사용 예:
  python perf_report.py                                  # anytime, horizon 180s
  python perf_report.py --smoke                          # 2문제, horizon 10s
  python perf_report.py --mode real --timelimits 30,60   # 채점 계약 게이트

산출: perf_out/perf_report[_tag].md + perf_results.jsonl(재실행 이어감) + png 6종.
"""

import os
from pathlib import Path

_HERE = Path(__file__).resolve().parent
PROJECT_ROOT = str(_HERE)
BASELINE_DIR = str(_HERE / "ogc2026" / "baseline")
EXAMPLE_DIR = str(_HERE / "ogc2026" / "alg_tester" / "example")
DEFAULT_OUT = str(_HERE / "perf_out")

SCHEMA = "perf"         # 관측 시계=json+preprocess+alns(진단 제외)·진단/관측 pre 분리 기준.
                        # 측정 규칙이 또 바뀌면 이름을 올려 과거 기록과 절대 안 섞이게 할 것.
TOP_FUNCS = 12
# 체크포인트: 10단위 고정 그리드(53s 같은 임의 기준 금지). 60s까지 10s 간격, 이후 30s 간격.
DEFAULT_CHECKPOINTS = "10,20,30,40,50,60,90,120,150,180"
DEFAULT_HORIZON = 180.0

import sys
import re
import io
import json
import glob
import time
import pstats
import cProfile
import argparse
import subprocess
import traceback
import datetime as _dt
from copy import deepcopy

# import 경로: 프로젝트 루트를 맨 앞, baseline은 맨 뒤(utils 폴백 전용).
# baseline/에도 myalgorithm.py(참조 greedy)가 있어 앞에 두면 그게 잡힌다.
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
if BASELINE_DIR not in sys.path:
    sys.path.append(BASELINE_DIR)

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import utils

from Phase0 import preprocess
from Phase1 import BuildBayAssignment
from Phase1.timing import init_timing
from Phase2 import DEFAULT_SCORING_PROFILE, PlaceAndCrane, Phase2Config
from Outer.alns import alns
from Outer.portfolio import default_portfolio
from Outer.realize import realize


def _phase2_cfg_for_report(scoring_profile: str | None = None) -> Phase2Config:
    if scoring_profile is None:
        return Phase2Config()
    return Phase2Config(scoring_profile=scoring_profile)


def _member0_for_report(scoring_profile: str | None = None):
    return deepcopy(default_portfolio(scoring_profile=scoring_profile)[0])


def _report_profile_tag(scoring_profile: str | None = None) -> str:
    return scoring_profile or DEFAULT_SCORING_PROFILE


def _report_run_key(kind: str, prob: str, value: float, scoring_profile: str | None = None) -> str:
    profile_tag = _report_profile_tag(scoring_profile)
    if kind == "anytime":
        return f"{prob}|anytime|h{value:g}|sp={profile_tag}"
    return f"{prob}|real|T{value:g}|sp={profile_tag}"

# 그림(matplotlib). 없으면 표만 내고 그림은 건너뛴다(보고서 본체와 독립 -> 안 깨짐).
try:
    import matplotlib
    matplotlib.use("Agg")              # 헤드리스/백그라운드 (디스플레이 불필요)
    import matplotlib.pyplot as _plt
    import numpy as _np
    _HAVE_MPL = True
except Exception:
    _HAVE_MPL = False


# ------------------------------------------------------------------ 공용 유틸

def _now():
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _natkey(path):
    m = re.findall(r"(\d+)", os.path.basename(path))
    return (int(m[-1]) if m else 0, os.path.basename(path))


def prob_key(path):
    return os.path.basename(os.path.dirname(path)) + "/" + os.path.splitext(os.path.basename(path))[0]


def fsync_append(path, line):
    with open(path, "a", encoding="utf-8") as f:
        f.write(line.rstrip("\n") + "\n")
        f.flush()
        os.fsync(f.fileno())


def atomic_write(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def load_done(jsonl_path):
    """run_key -> 현행 스키마의 최신 레코드. 다른 스키마 라인은 무시(자동 재측정)."""
    done = {}
    if os.path.exists(jsonl_path):
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    r = json.loads(ln)
                    if r.get("schema") == SCHEMA and r.get("run_key") and not r.get("error"):
                        done[r["run_key"]] = r
                except Exception:
                    pass
    return done


def _fmt(x, nd=3):
    if x is None:
        return "NA"
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    return str(x)


def _median(xs):
    xs = sorted(v for v in xs if v is not None)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else 0.5 * (xs[n // 2 - 1] + xs[n // 2])


def _mean(xs):
    xs = [v for v in xs if v is not None]
    return (sum(xs) / len(xs)) if xs else None


def _pctl(xs, q):
    xs = sorted(v for v in xs if v is not None)
    if not xs:
        return None
    i = min(len(xs) - 1, max(0, int(round(q * (len(xs) - 1)))))
    return xs[i]


def _top_funcs_from_profile(pr, top_n):
    """cProfile.Profile -> (self-time 상위 [(tottime,ncalls,label),...], 총 self-time).
    비중(%)은 각 함수 tottime / 총 self-time. 절대값이 아니라 비중으로 보려고 total을 함께 반환."""
    st = pstats.Stats(pr, stream=io.StringIO())
    rows = []
    total = 0.0
    for (fpath, line, name), (cc, nc, tt, ct, callers) in st.stats.items():
        rows.append((tt, nc, "%s (%s:%d)" % (name, os.path.basename(fpath), line)))
        total += tt
    rows.sort(reverse=True)
    return [(round(tt, 4), nc, label) for (tt, nc, label) in rows[:top_n]], total


# ------------------------------------------------------------------ anytime 모드

def _obj_at(best_events, t):
    """best_events(시각 오름차순)에서 시각 t의 incumbent objective. 해 없으면 None(= -1점 상황)."""
    val = None
    for et, f in best_events:
        if et <= t:
            val = f
        else:
            break
    return val


def _derive_anytime(rec, best_events, horizon, checkpoints):
    """관측 곡선 -> 체크포인트 품질/포화 지표. 전부 rec에 기록.

    원시 이벤트는 전부 보존하되, 파생 지표는 관측창(<= horizon) 이벤트로만
    계산한다: 마지막 realize가 딜라인을 넘겨 끝나며 찾은 개선(오버슛)은
    창 밖 데이터라 f_final/포화/마지막개선에 섞으면 행 표시와 절단 집계의
    기준이 어긋난다. 오버슛 크기 자체는 iter_ms_max가 잡는다(예약 산정용)."""
    rec["best_events"] = [[round(t, 3), f] for t, f in best_events]
    ev = [e for e in best_events if e[0] <= horizon + 1e-9]
    rec["n_overshoot_events"] = len(best_events) - len(ev)
    f_final = ev[-1][1] if ev else None
    f_first = ev[0][1] if ev else None
    rec["t_first_solution_s"] = round(ev[0][0], 3) if ev else None
    rec["n_improvements"] = max(0, len(ev) - 1)
    rec["t_last_improve_s"] = round(ev[-1][0], 3) if ev else None
    # 포화: 관측 지평 마지막 20% 동안 개선이 없었으면 수렴으로 판단
    rec["saturated"] = bool(ev and ev[-1][0] <= 0.8 * horizon)
    best_events = ev

    obj_at, gap_at = {}, {}
    for T in checkpoints:
        if T > horizon + 1e-9:
            continue                      # 관측 못 한 구간은 기록하지 않음(외삽 금지)
        k = ("%g" % T)
        v = _obj_at(best_events, T)
        obj_at[k] = v
        gap_at[k] = (round((v / f_final - 1.0) * 100.0, 3)
                     if (v is not None and f_final) else None)
    rec["obj_at_s"] = obj_at
    rec["gap_pct_at_s"] = gap_at

    # 최종 대비 1% 이내로 들어온 시각(수렴 속도)
    t1 = None
    if f_final:
        for et, f in best_events:
            if f <= f_final * 1.01:
                t1 = round(et, 3)
                break
    rec["t_to_1pct_s"] = t1
    rec["anytime_f_first"] = f_first
    rec["anytime_f_final"] = f_final


def run_one_anytime(path, horizon, checkpoints, do_profile=True, scoring_profile=None):
    """문제 1개 측정. 오염 차단이 최우선 설계 원칙:

    [1] 진단(pre_diag)과 관측(pre_run)은 각각 fresh preprocess로 분리한다.
        NFP 캐시가 PRE 객체 안에 살기 때문에, 같은 pre로 진단(Phase2 배치)을
        먼저 돌리면 관측의 첫 realize가 warm이 되어 곡선이 낙관 왜곡된다.
    [2] 관측 시계 = json 로드 + preprocess + ALNS 만 (배포 경로와 동일 구성).
        진단(Phase1/2/utils 검증) 시간은 시계에서 제외 -> 문제 크기에 따라
        진단이 관측 창을 다르게 깎아먹는 불공정 제거.
    [3] 프로파일(warm realize)은 관측이 끝난 뒤에만 수행한다."""
    key = prob_key(path)
    profile_tag = _report_profile_tag(scoring_profile)
    rec = {"schema": SCHEMA, "run_key": _report_run_key("anytime", key, horizon, scoring_profile),
            "mode": "anytime", "prob": key, "ts": _now(), "error": None,
            "horizon_s": horizon,
            "scoring_profile": profile_tag}
    t_all = time.perf_counter()
    try:
        t = time.perf_counter()
        with open(path, "r", encoding="utf-8") as f:
            prob = json.load(f)
        t_load = time.perf_counter() - t
        rec["t_load_s"] = round(t_load, 3)
        w = prob.get("weights", {})
        w1, w2, w3 = w.get("w1", 1.0), w.get("w2", 1.0), w.get("w3", 1.0)
        rec["n_blocks"] = len(prob["blocks"])
        rec["n_bays"] = len(prob["bays"])

        # ---- 진단 패스 (관측 시계 밖, pre_diag 사용: 전부 cold 계측) ----
        t = time.perf_counter()
        pre_diag = preprocess(prob)
        rec["t_phase0_pre"] = time.perf_counter() - t

        t = time.perf_counter()
        p1 = BuildBayAssignment(prob, pre_diag, None)
        rec["t_phase1_asgn"] = time.perf_counter() - t
        bay = list(p1.bay)

        t = time.perf_counter()
        p1out = init_timing(bay, prob, pre_diag)
        rec["t_phase1_timing"] = time.perf_counter() - t

        t = time.perf_counter()
        res = PlaceAndCrane(prob, p1out, pre_diag, _phase2_cfg_for_report(scoring_profile))
        rec["t_phase2_place_cold"] = time.perf_counter() - t

        feasible = bool(res.info.get("feasible", False))
        z1 = res.Z1 if (feasible and res.Z1 is not None) else None
        z2, z3 = p1out.Z2, p1out.Z3
        rec["construct_feasible"] = feasible
        rec["construct_Z1"], rec["construct_Z2"], rec["construct_Z3"] = z1, z2, z3
        rec["construct_forced"] = len(res.info.get("forced", []))
        rec["construct_objective"] = (w1 * z1 + w2 * z2 + w3 * z3) if feasible else None

        chk = utils.check_feasibility(prob, res.solution)
        rec["construct_utils_feasible"] = bool(chk["feasible"])
        rec["construct_utils_objective"] = chk["objective"]

        # ---- 관측 패스: fresh pre_run(캐시 cold) + 배포 시계 ----
        # 배포 시계 t0 = (alns 시작 시각) - (json 로드 + preprocess 시간).
        # 즉 이벤트 시각 = load + preprocess + alns 경과 -> 진단 시간만 정확히 제외.
        t = time.perf_counter()
        pre_run = preprocess(prob)
        t_pre_run = time.perf_counter() - t
        rec["t_phase0_pre_run"] = round(t_pre_run, 3)

        alns_start = time.perf_counter()
        t0 = alns_start - (t_load + t_pre_run)
        s_best, stats = alns(prob, pre_run, budget_s=None, cfg=_member0_for_report(scoring_profile),
                             deadline=t0 + horizon, deadline_s=horizon,
                             t0=t0)
        elapsed = stats.get("elapsed_s", time.perf_counter() - alns_start)

        iters = stats.get("iters", 0)
        f0 = stats.get("f0")
        fbest = stats.get("f_best")
        rec["alns_elapsed_s"] = elapsed
        rec["alns_deadline_scope"] = "load+preprocess+alns"
        rec["alns_total_to_end_s"] = time.perf_counter() - t0
        rec["alns_iters"] = iters
        rec["alns_iters_per_s"] = (iters / elapsed) if elapsed > 0 else 0.0
        rec["alns_ms_per_realize"] = (elapsed / iters * 1000.0) if iters else None
        rec["alns_f0"] = f0
        rec["alns_fbest"] = fbest
        rec["alns_improve_pct"] = ((f0 - fbest) / f0 * 100.0) if (f0 and fbest is not None and f0 > 0) else None
        rec["alns_stopped_by_deadline"] = bool(stats.get("stopped_by_deadline", False))
        rec["alns_feasible"] = bool(getattr(s_best, "feasible", False))
        rec["alns_best_forced"] = getattr(s_best, "forced", None)
        rec["alns_best_Z1"] = getattr(s_best, "Z1", None)
        rec["alns_best_Z2"] = getattr(s_best, "Z2", None)
        rec["alns_best_Z3"] = getattr(s_best, "Z3", None)
        rec["alns_best_objective"] = getattr(s_best, "objective", None)

        if getattr(s_best, "solution", None) is not None:
            alns_chk = utils.check_feasibility(prob, s_best.solution)
            rec["alns_utils_feasible"] = bool(alns_chk["feasible"])
            rec["alns_utils_objective"] = alns_chk["objective"]
            rec["alns_utils_Z1"] = alns_chk.get("obj1")
            rec["alns_utils_Z2"] = alns_chk.get("obj2")
            rec["alns_utils_Z3"] = alns_chk.get("obj3")
        else:
            rec["alns_utils_feasible"] = False
            rec["alns_utils_objective"] = None
            rec["alns_utils_Z1"] = rec["alns_utils_Z2"] = rec["alns_utils_Z3"] = None

        # ---- 곡선 -> 체크포인트/포화 지표 ----
        _derive_anytime(rec, stats.get("best_events") or [], horizon,
                        checkpoints)

        # 반복 1회 비용 분포(= 예약 시간 산정 근거: realize는 variant 도중 중단 불가)
        it_t = stats.get("iter_t") or []
        durs = [(b - a) * 1000.0 for a, b in zip(it_t, it_t[1:])]
        rec["iter_ms_p50"] = round(_pctl(durs, 0.50), 1) if durs else None
        rec["iter_ms_p90"] = round(_pctl(durs, 0.90), 1) if durs else None
        rec["iter_ms_max"] = round(max(durs), 1) if durs else None
        rec["iter_ms_n"] = len(durs)

        # ---- 병목 프로파일(warm realize 1회; 관측 종료 후라 곡선 오염 없음) ----
        if do_profile:
            pr = cProfile.Profile()
            pr.enable()
            realize(bay, prob, pre_run, _phase2_cfg_for_report(scoring_profile))
            pr.disable()
            rec["top_funcs"], rec["profile_total_self_s"] = _top_funcs_from_profile(pr, max(TOP_FUNCS, 20))

    except Exception:
        rec["error"] = traceback.format_exc()
    rec["t_total_measure"] = round(time.perf_counter() - t_all, 3)
    return rec


# ------------------------------------------------------------------ real 모드

_RUNNER_SRC = r'''# auto-generated by perf_report.py -- official grading contract replica
import importlib.util, json, os, sys, time, traceback

def main():
    prob_path, tl, alg_dir, out_path = sys.argv[1], float(sys.argv[2]), sys.argv[3], sys.argv[4]
    t_proc0 = time.perf_counter()
    sys.path.insert(0, alg_dir)
    spec = importlib.util.spec_from_file_location("myalgorithm", os.path.join(alg_dir, "myalgorithm.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with open(prob_path, "r", encoding="utf-8") as f:
        prob = json.load(f)
    t_import = time.perf_counter() - t_proc0

    res = {"ok": False, "t_import_s": round(t_import, 3), "t_algo_s": None,
           "solution": None, "traceback": None}
    t0 = time.perf_counter()
    try:
        sol = mod.algorithm(prob, tl)
        res["ok"] = True
        res["solution"] = sol
    except Exception:
        res["traceback"] = traceback.format_exc()
    res["t_algo_s"] = round(time.perf_counter() - t0, 3)

    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(res, f)
    os.replace(tmp, out_path)

main()
'''


def _kill_tree(proc):
    """프로세스 트리 강제 종료(algorithm은 4-워커 자식을 띄우므로 트리째 죽여야 함)."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True)
        else:
            proc.kill()
    except Exception:
        pass
    try:
        proc.wait(timeout=10)
    except Exception:
        pass


def run_one_real(path, tl, grace_frac, out_dir, runner_path, scoring_profile=None):
    """공식 채점 계약 그대로 1회: 새 프로세스에서 algorithm(prob, tl) 실행,
    tl*(1+grace)에 하드킬, §3.3 규칙(-1)으로 채점."""
    key = prob_key(path)
    profile_tag = _report_profile_tag(scoring_profile)
    rec = {"schema": SCHEMA, "run_key": _report_run_key("real", key, tl, scoring_profile),
            "mode": "real", "prob": key, "ts": _now(), "error": None,
            "timelimit_s": tl,
            "scoring_profile": profile_tag}
    out_json = os.path.join(out_dir, "_real_out_%s_T%g.json" % (key.replace("/", "_"), tl))
    try:
        if os.path.exists(out_json):
            os.remove(out_json)
        with open(path, "r", encoding="utf-8") as f:
            prob = json.load(f)
        rec["n_blocks"] = len(prob["blocks"])
        rec["n_bays"] = len(prob["bays"])

        kill_at = min(tl * (1.0 + grace_frac), tl + 60.0)
        rec["kill_at_s"] = round(kill_at, 1)

        t0 = time.perf_counter()
        env = os.environ.copy()
        if scoring_profile:
            env["OGC_SCORING_PROFILE"] = scoring_profile
        else:
            env.pop("OGC_SCORING_PROFILE", None)
        proc = subprocess.Popen(
            [sys.executable, "-u", runner_path, path, str(tl), PROJECT_ROOT, out_json],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
        killed = False
        try:
            proc.wait(timeout=kill_at)
        except subprocess.TimeoutExpired:
            killed = True
            _kill_tree(proc)
        rec["t_wall_process_s"] = round(time.perf_counter() - t0, 2)
        rec["killed_by_harness"] = killed

        res = None
        if os.path.exists(out_json):
            try:
                with open(out_json, "r", encoding="utf-8") as f:
                    res = json.load(f)
            except Exception:
                res = None

        if res:
            rec["t_import_s"] = res.get("t_import_s")
            rec["t_wall_algorithm_s"] = res.get("t_algo_s")

        # ---- §3.3 채점: 시간초과 = crash = infeasible = -1 ----
        if killed:
            rec["real_scored"] = "FAIL_TIMEOUT"       # 하드킬 = 서버라면 확정 -1
        elif res is None:
            rec["real_scored"] = "FAIL_CRASH"         # 결과 파일조차 없음
        elif not res.get("ok"):
            rec["real_scored"] = "FAIL_CRASH"
            rec["crash_traceback"] = (res.get("traceback") or "")[-2000:]
        else:
            t_algo = res.get("t_algo_s") or 0.0
            chk = utils.check_feasibility(prob, res["solution"])
            rec["real_feasible"] = bool(chk["feasible"])
            if not chk["feasible"]:
                rec["real_scored"] = "FAIL_INFEASIBLE"
                rec["infeasible_stage"] = chk.get("stage")
            elif t_algo > tl:
                rec["real_scored"] = "FAIL_TIMEOUT"   # 함수 내부 시계 기준 초과
            else:
                rec["real_scored"] = "OK"
                rec["real_objective"] = chk["objective"]
                rec["real_Z1"] = chk.get("obj1")
                rec["real_Z2"] = chk.get("obj2")
                rec["real_Z3"] = chk.get("obj3")

        t_algo = rec.get("t_wall_algorithm_s")
        if t_algo is not None:
            rec["margin_s"] = round(tl - t_algo, 2)
            rec["margin_frac"] = round((tl - t_algo) / tl, 4)
            rec["warn_thin_margin"] = bool(rec["margin_frac"] < 0.05 or rec["margin_s"] < 3.0)

        if os.path.exists(out_json):
            try:
                os.remove(out_json)
            except Exception:
                pass
    except Exception:
        rec["error"] = traceback.format_exc()
    return rec


# ------------------------------------------------------------------ 보고서

def _ck_cols(anyt):
    """anytime 레코드들의 체크포인트 컬럼(초, 오름차순 문자열 키)."""
    keys = set()
    for r in anyt:
        keys.update((r.get("obj_at_s") or {}).keys())
    return sorted(keys, key=float)


def _at_horizon(rec, H):
    """레코드를 관측 H초 시점으로 절단한 뷰. incumbent는 계단함수이므로
    '같은 런을 H까지만 관측했을 때'와 수학적으로 동일하다 -> 서로 다른
    horizon의 기록을 하나의 잣대(H)로 정확히 통일하는 유일한 방법."""
    ev = [e for e in (rec.get("best_events") or []) if e[0] <= H + 1e-9]
    if not ev:
        return None
    return {"events": ev,
            "f_first": ev[0][1],
            "f_final": ev[-1][1],
            "saturated": ev[-1][0] <= 0.8 * H}


def _select_anytime(records):
    """공정 집계 계약. 반환 (rows, agg, h_star):
      rows   = 문제당 1개(최장 horizon, 동률이면 최신) -- 표시용.
               같은 문제의 복수 관측이 표/플롯에 중복 유입되는 것을 차단.
      h_star = rows의 최빈 horizon(동률이면 큰 쪽) = 집계 기준 관측 길이.
      agg    = [(rec, view)] : 각 기록을 h_star로 절단(_at_horizon)한 뷰가
               존재하고 그 뷰가 포화인 것만. 모든 집계·곡선은 view 값만 사용
               -> 관측 길이가 다른 데이터가 같은 통계에 섞일 수 없다.
               (미포화 뷰 제외: '최종' 기준 미확정이라 gap이 왜곡됨)
    절대 objective @T 셀은 horizon과 무관하므로 rows 전체를 표시한다."""
    by = {}
    for r in records:
        if r.get("mode") != "anytime" or r.get("error"):
            continue
        p = r["prob"]
        cur = by.get(p)
        if (cur is None
                or (r.get("horizon_s") or 0) > (cur.get("horizon_s") or 0)
                or ((r.get("horizon_s") or 0) == (cur.get("horizon_s") or 0)
                    and (r.get("ts") or "") > (cur.get("ts") or ""))):
            by[p] = r
    rows = sorted(by.values(), key=lambda r: _natkey(r["prob"]))
    cnt = {}
    for r in rows:
        h = r.get("horizon_s") or 0
        cnt[h] = cnt.get(h, 0) + 1
    h_star = (max(sorted(cnt), key=lambda h: (cnt[h], h)) if cnt else None)
    agg = []
    for r in rows:
        if (r.get("horizon_s") or 0) + 1e-9 < (h_star or 0):
            continue                     # h_star보다 짧은 관측은 절단 불가 -> 집계 불가
        v = _at_horizon(r, h_star)
        if v and v["saturated"]:
            agg.append((r, v))
    return rows, agg, h_star


def build_report(records, planned_total, started, meta, out_dir=None):
    # 공정 집계 계약: 표/플롯 공통 선별(_select_anytime) -- 문제당 최장 관측 1개,
    # 집계는 전 기록을 h_star로 절단한 뷰(포화만)로 통일.
    anyt, agg_a, h_star = _select_anytime(records)
    real = [r for r in records if r.get("mode") == "real" and not r.get("error")]
    errs = [r for r in records if r.get("error")]

    L = []

    def img(name, alt):
        """플롯 파일이 실제로 생성된 경우에만 이미지 참조를 넣는다(깨진 링크 방지)."""
        if out_dir and os.path.exists(os.path.join(out_dir, name)):
            L.append(f"\n![{alt}]({name})")
    L.append("# OGC 2026 성능/시간제한 분석 보고서")
    L.append("")
    L.append(f"- 시작 {started} · 갱신 {_now()} · 진행 **{len(records)}/{planned_total}** "
             f"(anytime {len(anyt)} · real {len(real)} · 오류 {len(errs)})")
    L.append(f"- 설정: {meta}")
    L.append("")
    L.append("> 채점 계약(§3.2~3.3): timelimit 문제별 상이(수분~30분), **시간초과=crash=infeasible=−1점**, 자원 4코어/16GB. "
             "본 수치는 개발 머신 기준 → 절대 초가 아니라 **여유 비율(margin_frac)** 로 판단.")

    if not (anyt or real or errs):
        L.append("\n(측정된 문제가 아직 없습니다.)")
        return "\n".join(L) + "\n"

    def col(k):
        return [r.get(k) for r in anyt]

    # ---- 1) 진단 요약 ----
    if anyt:
        startup = [
            (r.get("t_phase0_pre", 0) + r.get("t_phase1_asgn", 0) +
             r.get("t_phase1_timing", 0) + r.get("t_phase2_place_cold", 0))
            for r in anyt
        ]
        L.append("\n## 1. 진단 요약 (anytime 성공 문제 기준)")
        L.append("")
        L.append("| 지표 | 평균 | 중앙값 | 최소 | 최대 |")
        L.append("|---|---|---|---|---|")

        def srow(name, xs, nd=3):
            xs2 = [v for v in xs if v is not None]
            if not xs2:
                L.append(f"| {name} | NA | NA | NA | NA |")
                return
            L.append(
                f"| {name} | {_fmt(_mean(xs2), nd)} | {_fmt(_median(xs2), nd)} | "
                f"{_fmt(min(xs2), nd)} | {_fmt(max(xs2), nd)} |"
            )

        srow("Phase0 preprocess (s)", col("t_phase0_pre"))
        srow("Phase1 assignment (s)", col("t_phase1_asgn"))
        srow("Phase1 timing (s)", col("t_phase1_timing"))
        srow("Phase2 first place cold NFP (s)", col("t_phase2_place_cold"))
        srow("Construction startup total (s)", startup)
        srow("첫 인증해 시각 t_first_solution (s)", col("t_first_solution_s"), 2)
        srow("ALNS iters/sec", col("alns_iters_per_s"), 2)
        srow("반복 1회 비용 p90 (ms)", col("iter_ms_p90"), 1)
        srow("반복 1회 비용 max (ms)", col("iter_ms_max"), 1)
        # improve%는 관측 길이에 의존 -> h_star 절단 뷰(agg_a)로만 집계(잣대 통일)
        srow(f"ALNS improve @{h_star:g}s (%)" if h_star else "ALNS improve (%)",
             [((v["f_first"] - v["f_final"]) / v["f_first"] * 100.0)
              for (_r, v) in agg_a if v["f_first"]], 2)
        srow("forced (ALNS best)", col("alns_best_forced"), 1)

    # ---- 2) anytime: 체크포인트 품질 (핵심 표) ----
    if anyt:
        cols = _ck_cols(anyt)
        L.append("\n## 2. 가상 timelimit 별 품질 (한 번의 긴 관측에서 후처리로 읽음)")
        L.append("")
        L.append("셀 = 그 시각의 incumbent objective (최종 대비 +gap%). `-1` = 그 시각까지 인증해 없음(서버라면 **−1점**).")
        L.append("")
        L.append("| prob | h(s) | " + " | ".join(f"{c}s" for c in cols) + " |")
        L.append("|---|---|" + "|".join(["---"] * len(cols)) + "|")
        for r in sorted(anyt, key=lambda r: _natkey(r["prob"])):
            cells = []
            oa, ga = r.get("obj_at_s") or {}, r.get("gap_pct_at_s") or {}
            for c in cols:
                if c not in oa:
                    cells.append("")               # 이 레코드의 horizon 밖(관측 안 함)
                elif oa[c] is None:
                    cells.append("**-1**")
                else:
                    g = ga.get(c)
                    cells.append(f"{oa[c]:.0f}" + (f" (+{g:.1f}%)" if g is not None else ""))
            L.append(f"| {r['prob']} | {r.get('horizon_s'):g} | " + " | ".join(cells) + " |")
        # 컬럼 요약 — 모든 기록을 h_star로 절단한 뷰(포화만)로 집계: 잣대·표본 완전 통일.
        # 행의 gap은 그 행 자신의 관측 기준, 아래 집계는 h_star 절단 기준(라벨 명시).
        if agg_a and h_star:
            med_g, n_m1 = [], []
            for c in cols:
                if float(c) > h_star + 1e-9:
                    med_g.append(None)   # 집계 기준 밖: 빈칸
                    n_m1.append(None)
                    continue
                gs, m1 = [], 0
                for (r, v) in agg_a:
                    o = (r.get("obj_at_s") or {}).get(c)
                    if o is None:
                        m1 += 1          # 그 시각까지 해 없음(-1 위험)
                    elif v["f_final"]:
                        gs.append((o / v["f_final"] - 1.0) * 100.0)
                med_g.append(_median(gs))
                n_m1.append(m1)
            L.append(f"| **중앙값 gap (포화 {len(agg_a)}개 · h={h_star:g}s 절단 기준)** | | " +
                     " | ".join((_fmt(g, 1) + "%") if g is not None else "" for g in med_g) + " |")
            L.append("| **−1 위험 문제수** | | " + " | ".join(str(n) if n is not None else "" for n in n_m1) + " |")
        n_excl = len(anyt) - len(agg_a)
        if n_excl:
            L.append(f"\n> **{n_excl}개**는 gap 집계·곡선에서 제외(h={h_star:g}s 절단 시 미포화이거나 관측이 그보다 짧음).")
        img("anytime_curves.png", "anytime 품질 곡선")
        img("quality_vs_budget.png", "timelimit별 품질")

        # ---- 2.2) 동일 문제 복수 관측 (집계엔 절대 혼입 안 됨; 기록은 남김) ----
        multi = {}
        for r in records:
            if r.get("mode") == "anytime" and not r.get("error"):
                multi.setdefault(r["prob"], []).append(r.get("horizon_s"))
        multi = {p: sorted(set(hs)) for p, hs in multi.items() if len(set(hs)) > 1}
        if multi:
            used = {r["prob"]: r.get("horizon_s") for r in anyt}
            L.append("\n### 2.2 동일 문제 복수 관측")
            L.append("")
            for p in sorted(multi, key=_natkey):
                hs = " · ".join(f"h={h:g}s" for h in multi[p])
                L.append(f"- {p}: {hs} → 표시는 h={used.get(p):g}s(최장), 집계는 h={h_star:g}s 절단 뷰")

        # ---- 2.1) 포화/수렴 ----
        L.append("\n### 2.1 수렴/포화 (\"몇 초면 충분한가\")")
        L.append("")
        L.append("| prob | 첫 해(s) | 최종1%이내 도달(s) | 마지막 개선(s) | 개선횟수 | 포화@h |")
        L.append("|---|---|---|---|---|---|")
        for r in sorted(anyt, key=lambda r: _natkey(r["prob"])):
            sat = bool(r.get("saturated"))
            L.append("| {p} | {fs} | {t1} | {tl} | {n} | {sat} |".format(
                p=r["prob"], fs=_fmt(r.get("t_first_solution_s"), 1),
                # 미포화면 '최종' 기준이 미확정이라 1% 도달 시각은 무의미 -> NA
                t1=_fmt(r.get("t_to_1pct_s"), 1) if sat else "NA",
                tl=_fmt(r.get("t_last_improve_s"), 1),
                n=r.get("n_improvements"),
                sat=("Y" if sat else f"N(h={r.get('horizon_s'):g}s 부족)")))
        n_unsat = sum(1 for r in anyt if not r.get("saturated"))
        if n_unsat:
            L.append(f"\n> **{n_unsat}개 문제가 관측 지평 끝까지 개선 중** — 더 긴 --horizon 재관측 권장.")

    # ---- 3) 구성 phase 비중 ----
    if anyt:
        tot_pre = sum(v or 0 for v in col("t_phase0_pre"))
        tot_asg = sum((r.get("t_phase1_asgn", 0) or 0) + (r.get("t_phase1_timing", 0) or 0) for r in anyt)
        tot_p2 = sum(v or 0 for v in col("t_phase2_place_cold"))
        tot = tot_pre + tot_asg + tot_p2 or 1.0
        L.append("\n## 3. 구성(startup) 시간의 phase별 비중")
        L.append("")
        L.append(f"- Phase0 preprocess : **{tot_pre/tot*100:5.1f}%** ({tot_pre:.1f}s)")
        L.append(f"- Phase1 배정+타이밍 : **{tot_asg/tot*100:5.1f}%** ({tot_asg:.1f}s)")
        L.append(f"- Phase2 첫배치      : **{tot_p2/tot*100:5.1f}%** ({tot_p2:.1f}s)  ← lazy NFP 빌드(문제당 1회)")
        L.append("")
        L.append("> 반복 1회 비용은 cold가 아니라 **iter ms**(warm). 해 품질 ∝ **iters/sec**이므로 낮은 문제가 실질 병목.")
        img("phase_dist.png", "phase 시간 분포(크기별)")

    # ---- 4) 병목 함수 ----
    n_prof = sum(1 for r in anyt if r.get("profile_total_self_s"))
    if n_prof:
        share_sum = {}
        for r in anyt:
            tot = r.get("profile_total_self_s")
            if not tot:
                continue
            for (tt, nc, label) in (r.get("top_funcs") or []):
                share_sum[label] = share_sum.get(label, 0.0) + 100.0 * (tt or 0.0) / tot
        top = sorted(((k, v / n_prof) for k, v in share_sum.items()),
                     key=lambda kv: kv[1], reverse=True)[:TOP_FUNCS]
        L.append("\n## 4. 병목 함수 (realize self-time 비중, 전 문제 평균)")
        L.append("")
        if top:
            L.append("| 함수 | 평균 비중 (%) |")
            L.append("|---|---|")
            for label, sh in top:
                L.append(f"| `{label}` | {sh:.1f} |")
            L.append("\n> realize hot loop CPU의 비중(%). 상위 함수 최적화 -> iters/sec 상승.")
        img("bottleneck_share.png", "병목 비중 분포")
        img("bottleneck_by_size.png", "문제 크기별 병목 비중 변화")

    # ---- 5) real: 채점 계약 컴플라이언스 ----
    if real:
        tls = sorted({r.get("timelimit_s") for r in real})
        probs_r = sorted({r["prob"] for r in real}, key=_natkey)
        n_fail = sum(1 for r in real if r.get("real_scored") != "OK")
        L.append("\n## 5. 채점 계약 컴플라이언스 (실제 algorithm(prob,T), 새 프로세스+하드킬)")
        L.append("")
        if n_fail:
            L.append(f"> ⚠️ **{n_fail}/{len(real)} 런이 −1 (시간초과/infeasible/crash)**")
            L.append("")
        L.append("| prob | " + " | ".join(f"T={t:g}s" for t in tls) + " |")
        L.append("|---|" + "|".join(["---"] * len(tls)) + "|")
        by = {(r["prob"], r.get("timelimit_s")): r for r in real}
        for p in probs_r:
            cells = []
            for t in tls:
                r = by.get((p, t))
                if not r:
                    cells.append("")
                    continue
                sc = r.get("real_scored")
                if sc == "OK":
                    cells.append(f"OK {_fmt(r.get('real_objective'), 0)} "
                                 f"(여유 {int(100*(r.get('margin_frac') or 0))}%)")
                elif sc == "FAIL_TIMEOUT":
                    w = r.get("t_wall_algorithm_s") or r.get("t_wall_process_s")
                    cells.append(f"**TIMEOUT** {'kill' if r.get('killed_by_harness') else ''}{_fmt(w,0)}s")
                elif sc == "FAIL_INFEASIBLE":
                    cells.append(f"**INFEAS** st{r.get('infeasible_stage')}")
                else:
                    cells.append("**CRASH**")
            L.append(f"| {p} | " + " | ".join(cells) + " |")
        okm = [r.get("margin_frac") for r in real if r.get("real_scored") == "OK"]
        L.append("")
        L.append(f"- OK {len(real)-n_fail}/{len(real)} · OK 런 여유비율 margin_frac 중앙값 "
                 f"{_fmt(_median(okm), 3)} (안전 기준: ≥0.05 그리고 margin_s ≥3s)")
        img("margin_scatter.png", "timelimit 대비 소요/여유")

    # ---- 6) 예약(reserve) 산정 근거 ----
    L.append("\n## 6. 시간제한 정책 산정 근거 (실측)")
    L.append("")
    if anyt:
        fs_max = max((r.get("t_first_solution_s") or 0) for r in anyt)
        im_max = max((r.get("iter_ms_max") or 0) for r in anyt) / 1000.0
        L.append(f"- 첫 인증해 최악 시각: **{fs_max:.1f}s** (이보다 짧은 timelimit이면 그 문제는 −1 확정)")
        L.append(f"- 반복 1회 최악 비용: **{im_max:.1f}s** (realize는 variant 도중 중단 불가 → 딜라인 초과분의 하한)")
    L.append("")
    L.append("> 딜라인은 고정 상수가 아니라 `timelimit - reserve`로 인자에서 유도할 것. "
             "reserve 크기는 위 실측치(첫 해 시각·반복 최악 비용)와 real 모드의 여유 비율로 산정.")

    # ---- 7) 상관 ----
    if anyt:
        L.append("\n## 7. 상관")
        L.append("")
        nb = [(r.get("n_blocks"), r.get("alns_iters_per_s"))
              for r in anyt if r.get("n_blocks") and r.get("alns_iters_per_s") is not None]
        if nb:
            slow = sorted(nb, key=lambda p: p[1])[:3]
            L.append("- 처리량 최저 top-3 (n_blocks, iters/s): " +
                     ", ".join(f"({b}, {ips:.2f})" for b, ips in slow))
        fz = [(r.get("alns_best_forced"), r.get("alns_utils_Z1"))
              for r in anyt
              if r.get("alns_best_forced") is not None and r.get("alns_utils_Z1") is not None]
        if len(fz) >= 2:
            xs = [a for a, _ in fz]
            ys = [b for _, b in fz]
            mx, my = _mean(xs), _mean(ys)
            num = sum((a - mx) * (b - my) for a, b in fz)
            den = (sum((a - mx) ** 2 for a in xs) * sum((b - my) ** 2 for b in ys)) ** 0.5
            corr = (num / den) if den else 0.0
            L.append(f"- forced(강제배치) ↔ Z1(지연) 상관계수: **{corr:+.2f}** "
                     f"(+1에 가까울수록 강제배치 줄이기가 Z1 최우선 레버)")

    # ---- 8) 문제별 상세 ----
    if anyt:
        L.append("\n## 8. 문제별 상세 (anytime)")
        L.append("")
        L.append("| prob | n_blk | P0(s) | P1(s) | P2cold(s) | ALNS(s) | iters | iters/s | improve% | 포화 | forced | Z1 | objective | feas |")
        L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for r in sorted(anyt, key=lambda rec: _natkey(rec["prob"])):
            p1t = (r.get("t_phase1_asgn", 0) or 0) + (r.get("t_phase1_timing", 0) or 0)
            L.append("| {p} | {nb} | {p0} | {p1} | {p2} | {ae} | {it} | {ips} | {imp} | {sat} | {frc} | {z1} | {obj} | {fe} |".format(
                p=r["prob"], nb=r.get("n_blocks", ""),
                p0=_fmt(r.get("t_phase0_pre"), 3), p1=_fmt(p1t, 3),
                p2=_fmt(r.get("t_phase2_place_cold"), 3),
                ae=_fmt(r.get("alns_elapsed_s"), 2),
                it=r.get("alns_iters", ""), ips=_fmt(r.get("alns_iters_per_s"), 2),
                imp=_fmt(r.get("alns_improve_pct"), 2),
                sat="Y" if r.get("saturated") else "N",
                frc=r.get("alns_best_forced", ""),
                z1=_fmt(r.get("alns_utils_Z1"), 0),
                obj=_fmt(r.get("alns_utils_objective"), 0),
                fe="Y" if r.get("alns_utils_feasible") else "N"))

    if errs:
        L.append("\n## Errors")
        for r in errs:
            head = (r.get("error") or "").strip().splitlines()[-1:] or [""]
            L.append(f"- {r.get('run_key', r.get('prob'))}: {head[0]}")
    return "\n".join(L) + "\n"


# ------------------------------------------------------------------ 그림

def make_plots(records, out_dir):
    """png 5종. matplotlib 없거나 데이터 부족이면 조용히 건너뜀(보고서 본체와 독립).
    라벨은 폰트 문제 회피 위해 영문."""
    if not _HAVE_MPL:
        return
    # 보고서와 동일한 공정 집계 계약(_select_anytime):
    #   곡선/gap 플롯 = h_star 절단 뷰(포화만) -> 모든 곡선이 같은 창 [0, h_star],
    #   phase/병목 플롯 = rows(문제당 1개; horizon과 무관한 지표라 포화 불문).
    anyt, agg_a, h_star = _select_anytime(records)
    real = [r for r in records if r.get("mode") == "real" and not r.get("error")]

    # (1) anytime_curves.png : 정규화 품질 q(t) 스텝 곡선 (h_star 절단 뷰, 문제별 + 중앙값)
    try:
        curves = []
        for (r, v) in agg_a:
            ev, f0, ff = v["events"], v["f_first"], v["f_final"]
            if len(ev) >= 2 and f0 and ff is not None and f0 > ff:
                t = [e[0] for e in ev] + [h_star]
                q = [(f0 - e[1]) / (f0 - ff) for e in ev]
                q.append(q[-1])
                curves.append((r["prob"], t, q))
        if curves:
            fig, ax = _plt.subplots(figsize=(10, 6))
            tmin = max(0.3, min(c[1][0] for c in curves) * 0.7)
            for (p, t, q) in curves:
                ax.step([max(x, tmin) for x in t], q, where="post", alpha=0.35, lw=1.2)
            # 모든 곡선이 동일 창 [0, h_star] -> 중앙값도 전 구간 동일 표본
            grid = _np.geomspace(tmin, h_star, 120)
            med = []
            for g in grid:
                vals = []
                for (p, t, q) in curves:
                    idx = 0
                    for i, x in enumerate(t):
                        if x <= g:
                            idx = i
                        else:
                            break
                    vals.append(q[idx] if t[0] <= g else 0.0)
                med.append(_np.median(vals) if vals else _np.nan)
            ax.plot(grid, med, color="#0f172a", lw=2.8,
                    label=f"median (n={len(curves)})")
            for c in (float(x) for x in _ck_cols([r for (r, _v) in agg_a])):
                if tmin < c < h_star:                     # 체크포인트 격자(하드코딩 없음)
                    ax.axvline(c, color="#94a3b8", ls=":", lw=0.8)
            ax.set_xscale("log")
            ax.set_xlabel("elapsed since algorithm entry (s, log)")
            ax.set_ylabel("normalized quality  (f0-f)/(f0-f_final)")
            ax.set_ylim(-0.02, 1.02)
            ax.set_title(f"Anytime quality curves (saturated @ {h_star:g}s window)")
            ax.grid(True, which="both", alpha=0.25)
            ax.legend(loc="lower right")
            fig.tight_layout()
            fig.savefig(os.path.join(out_dir, "anytime_curves.png"), dpi=110)
            _plt.close(fig)
    except Exception:
        try: _plt.close("all")
        except Exception: pass

    # (2) quality_vs_budget.png : 체크포인트별 gap% 중앙값 + IQR 밴드
    #     h_star 절단 뷰(포화만) -> 전 지점 동일 표본·동일 잣대
    try:
        cols = [c for c in _ck_cols([r for (r, _v) in agg_a])
                if h_star and float(c) <= h_star + 1e-9]
        xs, med, q1, q3, n_m1 = [], [], [], [], []
        for c in cols:
            gs, m1 = [], 0
            for (r, v) in agg_a:
                o = (r.get("obj_at_s") or {}).get(c)
                if o is None:
                    m1 += 1
                elif v["f_final"]:
                    gs.append((o / v["f_final"] - 1.0) * 100.0)
            if gs:
                xs.append(float(c))
                med.append(_median(gs))
                q1.append(_pctl(gs, 0.25))
                q3.append(_pctl(gs, 0.75))
                n_m1.append(m1)
        if len(xs) >= 2:
            fig, ax = _plt.subplots(figsize=(9, 5.5))
            ax.fill_between(xs, q1, q3, alpha=0.25, color="#3b82f6", label="IQR")
            ax.plot(xs, med, "-o", color="#1d4ed8", lw=2, label="median gap vs final")
            for x, m in zip(xs, n_m1):
                if m:
                    ax.annotate(f"-1 x{m}", (x, max(med) if med else 1), color="#dc2626",
                                fontsize=8, ha="center")
            ax.set_xscale("log")
            ax.set_xlabel("hypothetical timelimit T (s, log)")
            ax.set_ylabel("objective gap vs final best (%)")
            ax.set_title("Quality left on the table at each timelimit (saturated runs)")
            ax.grid(True, which="both", alpha=0.25)
            ax.legend()
            fig.tight_layout()
            fig.savefig(os.path.join(out_dir, "quality_vs_budget.png"), dpi=110)
            _plt.close(fig)
    except Exception:
        try: _plt.close("all")
        except Exception: pass

    # (3) margin_scatter.png : real 모드 소요/T 산점 (채점 결과 색)
    try:
        if real:
            colors = {"OK": "#16a34a", "FAIL_TIMEOUT": "#dc2626",
                      "FAIL_INFEASIBLE": "#d97706", "FAIL_CRASH": "#111827"}
            fig, ax = _plt.subplots(figsize=(9, 5.5))
            seen = set()
            for r in real:
                tl = r.get("timelimit_s")
                w = r.get("t_wall_algorithm_s") or r.get("t_wall_process_s")
                if not (tl and w):
                    continue
                sc = r.get("real_scored") or "FAIL_CRASH"
                lab = sc if sc not in seen else None
                seen.add(sc)
                ax.scatter(tl, w / tl, s=48, color=colors.get(sc, "#64748b"),
                           label=lab, zorder=3,
                           marker="x" if sc == "FAIL_CRASH" else "o")
            ax.axhline(1.0, color="#dc2626", ls="--", lw=1.5)
            ax.axhline(0.95, color="#94a3b8", ls=":", lw=1.2)
            ax.text(0.02, 1.005, "timelimit (score -1 above)", transform=ax.get_yaxis_transform(),
                    color="#dc2626", fontsize=8, va="bottom")
            ax.set_xscale("log")
            ax.set_xlabel("timelimit T (s, log)")
            ax.set_ylabel("algorithm wall-time / T")
            ax.set_title("Grading-contract compliance: time used vs limit")
            ax.grid(True, which="both", alpha=0.25)
            ax.legend(loc="best")
            fig.tight_layout()
            fig.savefig(os.path.join(out_dir, "margin_scatter.png"), dpi=110)
            _plt.close(fig)
    except Exception:
        try: _plt.close("all")
        except Exception: pass

    # (4) phase_dist.png : Phase0/1/2-cold(s) + iter ms, 크기별 violin + 개별 점
    ok = [r for r in anyt if r.get("n_blocks")]
    if len(ok) >= 2:
        try:
            sizes = sorted({r["n_blocks"] for r in ok})
            def _p1(r):
                return (r.get("t_phase1_asgn", 0) or 0) + (r.get("t_phase1_timing", 0) or 0)
            metrics = [("Phase0 preprocess (s)", lambda r: r.get("t_phase0_pre")),
                       ("Phase1 assign+timing (s)", _p1),
                       ("Phase2 first-place cold (s)", lambda r: r.get("t_phase2_place_cold")),
                       ("iteration cost p90 (ms)", lambda r: r.get("iter_ms_p90"))]
            fig, axes = _plt.subplots(2, 2, figsize=(11, 8))
            for ax, (title, fn) in zip(axes.flat, metrics):
                data = [[fn(r) for r in ok if r["n_blocks"] == s and fn(r) is not None] for s in sizes]
                vpos = [i for i, d in enumerate(data) if len(d) >= 2]
                if vpos:
                    ax.violinplot([data[i] for i in vpos], positions=vpos, showmedians=True, widths=0.7)
                for i, d in enumerate(data):
                    if d:
                        ax.scatter([i] * len(d), d, s=14, color="#334155", alpha=0.55, zorder=3)
                ax.set_xticks(range(len(sizes)))
                ax.set_xticklabels(sizes)
                ax.set_xlabel("n_blocks"); ax.set_title(title); ax.grid(True, axis="y", alpha=0.3)
            fig.suptitle("Phase time distribution by problem size", fontsize=13)
            fig.tight_layout()
            fig.savefig(os.path.join(out_dir, "phase_dist.png"), dpi=110)
            _plt.close(fig)
        except Exception:
            try: _plt.close("all")
            except Exception: pass

        # (5) bottleneck_share.png : 상위 함수의 realize self-time 비중(%) 분포
        try:
            shares = {}
            for r in ok:
                tot = r.get("profile_total_self_s")
                if not tot:
                    continue
                for (tt, nc, label) in (r.get("top_funcs") or []):
                    shares.setdefault(label, []).append(100.0 * (tt or 0.0) / tot)
            items = sorted(shares.items(), key=lambda kv: sum(kv[1]) / len(kv[1]), reverse=True)[:8]
            if items:
                items = items[::-1]                      # 큰 값이 위로
                labels = [k for k, _ in items]
                vals = [v for _, v in items]
                fig, ax = _plt.subplots(figsize=(10, 5))
                ax.boxplot(vals, vert=False, tick_labels=labels, showmeans=True)
                ax.set_xlabel("realize self-time share (%)")
                ax.set_title("Bottleneck function share distribution (all problems)")
                ax.grid(True, axis="x", alpha=0.3)
                fig.tight_layout()
                fig.savefig(os.path.join(out_dir, "bottleneck_share.png"), dpi=110)
                _plt.close(fig)
        except Exception:
            try: _plt.close("all")
            except Exception: pass

        # (6) bottleneck_by_size.png : 상위 함수 비중(%) vs 문제 크기
        #     -> 병목이 크기에 따라 바뀌는지(스케일링 진단)를 직접 보여준다.
        try:
            pts = {}   # label -> [(n_blocks, share%)]
            for r in ok:
                tot = r.get("profile_total_self_s")
                if not tot:
                    continue
                for (tt, nc, label) in (r.get("top_funcs") or []):
                    pts.setdefault(label, []).append((r["n_blocks"], 100.0 * (tt or 0.0) / tot))
            top_labels = sorted(pts.items(),
                                key=lambda kv: -(sum(s for _, s in kv[1]) / len(kv[1])))[:6]
            sizes_all = sorted({r["n_blocks"] for r in ok})
            if top_labels and len(sizes_all) >= 2:
                fig, ax = _plt.subplots(figsize=(10, 5.5))
                for label, vals in top_labels:
                    # 같은 크기 여러 문제 -> 크기별 평균선 + 개별 점
                    by_size = {}
                    for nbk, sh in vals:
                        by_size.setdefault(nbk, []).append(sh)
                    xs = sorted(by_size)
                    ys = [sum(by_size[x]) / len(by_size[x]) for x in xs]
                    ln, = ax.plot(xs, ys, "-o", ms=5, lw=1.8, alpha=0.9, label=label[:52])
                    for nbk, sh in vals:
                        ax.scatter(nbk, sh, s=12, color=ln.get_color(), alpha=0.35, zorder=2)
                ax.set_xlabel("n_blocks (problem size)")
                ax.set_ylabel("realize self-time share (%)")
                ax.set_title("Bottleneck share vs problem size (top functions)")
                ax.grid(True, alpha=0.3)
                ax.legend(fontsize=8, loc="best")
                fig.tight_layout()
                fig.savefig(os.path.join(out_dir, "bottleneck_by_size.png"), dpi=110)
                _plt.close(fig)
        except Exception:
            try: _plt.close("all")
            except Exception: pass


# ------------------------------------------------------------------ 실행

def _warmup_jit(probs, scoring_profile=None):
    for p in probs:
        try:
            with open(p, "r", encoding="utf-8") as f:
                prob = json.load(f)
            pre = preprocess(prob)
            p1 = BuildBayAssignment(prob, pre, None)
            realize(list(p1.bay), prob, pre, _phase2_cfg_for_report(scoring_profile))
            return
        except Exception:
            continue


def parse_args():
    ap = argparse.ArgumentParser(description="OGC 2026 solver perf / time-limit harness")
    ap.add_argument("--mode", choices=["anytime", "real", "both"], default="anytime")
    ap.add_argument("--horizon", type=float, default=None,
                    help=f"anytime 관측 지평(초). 기본 {DEFAULT_HORIZON:g}, --smoke 시 10")
    ap.add_argument("--checkpoints", default=DEFAULT_CHECKPOINTS,
                    help=f"가상 timelimit 체크포인트 CSV (기본 {DEFAULT_CHECKPOINTS})")
    ap.add_argument("--timelimits", default="30,60",
                    help="real 모드 timelimit CSV (기본 30,60)")
    ap.add_argument("--grace-frac", type=float, default=0.15,
                    help="real 모드 하드킬 유예 비율 (기본 0.15 -> T*1.15에 kill)")
    ap.add_argument("--probs", default=None, help="문제 이름 부분일치 필터 CSV (예: prob_1,prob_7)")
    ap.add_argument("--prob-dir", action="append", default=None,
                    help="문제 JSON 폴더(반복 지정 가능). 지정 시 *.json 전체 사용")
    ap.add_argument("--max-probs", type=int, default=None)
    ap.add_argument("--smoke", action="store_true",
                    help="빠른 연기시험: 2문제, horizon 10s, 프로파일 생략, 데이터 없으면 example 폴더 사용")
    ap.add_argument("--scoring-profile", default=None,
                    help="Phase2 scoring profile override for report runs")
    ap.add_argument("--tag", default="", help="보고서 파일명 접미사")
    ap.add_argument("--out", default=DEFAULT_OUT)
    return ap.parse_args()


def discover_probs(args, log):
    if args.prob_dir:
        dirs = [os.path.abspath(d) for d in args.prob_dir]
        pattern = "*.json"
    else:
        dirs = [str(_HERE / "train")]
        pattern = "prob_*.json"
    files = []
    for d in dirs:
        files += glob.glob(os.path.join(d, pattern))
    files = sorted(set(files), key=_natkey)
    if not files and args.smoke and os.path.isdir(EXAMPLE_DIR):
        log(f"문제 폴더가 비어 있어 --smoke 폴백: {EXAMPLE_DIR}")
        files = sorted(glob.glob(os.path.join(EXAMPLE_DIR, "*.json")), key=_natkey)
    if not files:
        log("오류: 문제 JSON을 찾지 못했습니다. 찾아본 위치:")
        for d in dirs:
            log(f"  - {d} ({'존재' if os.path.isdir(d) else '없음'})")
        log(f"힌트: --prob-dir {EXAMPLE_DIR} (대회 예제) 또는 train/ 폴더를 채우세요.")
        return []
    if args.probs:
        pats = [p.strip() for p in args.probs.split(",") if p.strip()]
        files = [f for f in files if any(p in os.path.basename(f) for p in pats)]
    if args.max_probs:
        files = files[:args.max_probs]
    return files


def main():
    args = parse_args()
    if args.smoke:
        if args.horizon is None:
            args.horizon = 10.0
        if args.max_probs is None:
            args.max_probs = 2
    if args.horizon is None:
        args.horizon = DEFAULT_HORIZON
    horizon = float(args.horizon)
    checkpoints = sorted({float(x) for x in args.checkpoints.split(",") if x.strip()} | {horizon})
    timelimits = [float(x) for x in args.timelimits.split(",") if x.strip()]

    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)
    jsonl_path = os.path.join(out_dir, "perf_results.jsonl")
    tag = ("_" + args.tag) if args.tag else ""
    report_path = os.path.join(out_dir, f"perf_report{tag}.md")
    log_path = os.path.join(out_dir, "perf_run.log")

    def log(msg):
        line = f"[{_now()}] {msg}"
        print(line, flush=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()

    probs = discover_probs(args, log)
    if not probs:
        return

    done = load_done(jsonl_path)
    records = list(done.values())

    # 이번 실행의 작업 목록 (이미 완료된 run_key는 건너뜀)
    tasks = []
    if args.mode in ("anytime", "both"):
        for p in probs:
            rk = _report_run_key("anytime", prob_key(p), horizon, args.scoring_profile)
            if rk not in done:
                tasks.append(("anytime", p, None, rk))
    if args.mode in ("real", "both"):
        for p in probs:
            for tl in timelimits:
                rk = _report_run_key("real", prob_key(p), tl, args.scoring_profile)
                if rk not in done:
                    tasks.append(("real", p, tl, rk))
    planned_total = len(tasks) + len(records)

    meta = (f"mode={args.mode} · anytime horizon={horizon:g}s(member0 단일 ALNS, 시계=load+preprocess+alns, 진단 제외) · "
            f"real timelimits={timelimits} grace={args.grace_frac:g} · "
            f"scoring_profile={args.scoring_profile or DEFAULT_SCORING_PROFILE} · "
            f"체크포인트={['%g' % c for c in checkpoints]}")
    started = _now()
    log(f"START probs={len(probs)} tasks={len(tasks)} resume={len(done)} out={out_dir}")
    log(f"  {meta}")

    runner_path = os.path.join(out_dir, "_real_runner.py")
    if any(t[0] == "real" for t in tasks):
        atomic_write(runner_path, _RUNNER_SRC)

    if any(t[0] == "anytime" for t in tasks):
        log("numba JIT warmup...")
        _warmup_jit(probs, args.scoring_profile)
        log("warmup done.")

    for kind, path, tl, rk in tasks:
        if kind == "anytime":
            rec = run_one_anytime(
                path, horizon, checkpoints,
                do_profile=not args.smoke,
                scoring_profile=args.scoring_profile,
            )
        else:
            rec = run_one_real(path, tl, args.grace_frac, out_dir, runner_path, args.scoring_profile)
        fsync_append(jsonl_path, json.dumps(rec, ensure_ascii=False))
        records.append(rec)
        make_plots(records, out_dir)   # 그림 먼저(보고서가 존재하는 그림만 참조)
        atomic_write(report_path, build_report(records, planned_total, started, meta, out_dir))
        if rec.get("error"):
            log(f"  {rk:34s} ERROR -- recorded and continuing")
        elif kind == "anytime":
            log("  {k:34s} feas={fe} first={fs}s last_imp={li}s sat={sat} iters={it} obj={o} ({t}s)".format(
                k=rk, fe=rec.get("alns_utils_feasible"),
                fs=_fmt(rec.get("t_first_solution_s"), 1),
                li=_fmt(rec.get("t_last_improve_s"), 1),
                sat="Y" if rec.get("saturated") else "N",
                it=rec.get("alns_iters"),
                o=_fmt(rec.get("alns_utils_objective"), 0),
                t=rec.get("t_total_measure")))
        else:
            log("  {k:34s} {sc} algo={ta}s proc={tp}s margin={m} killed={kd}".format(
                k=rk, sc=rec.get("real_scored"),
                ta=_fmt(rec.get("t_wall_algorithm_s"), 1),
                tp=_fmt(rec.get("t_wall_process_s"), 1),
                m=_fmt(rec.get("margin_frac"), 3),
                kd=rec.get("killed_by_harness")))

    log(f"DONE {len(records)}/{planned_total} report={report_path}")


if __name__ == "__main__":
    main()
