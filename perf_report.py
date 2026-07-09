#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""
perf_report.py -- OGC 2026 솔버 성능 분석 하니스 (단일 파일, 백그라운드/크래시 내성).

이 파일 하나만 실행하면:
    python perf_report.py
prob_1 부터 모든 인스턴스에 대해 성능을 측정하고, 성능 병목의 원인 파악에 핵심적인
지표를 보고서(Markdown)로 산출한다.

측정하는 것 (문제당):
  * phase별 수행시간 : Phase0(preprocess) / Phase1(배정+타이밍) / Phase2(첫 배치=cold NFP 빌드).
  * ALNS 처리량      : 고정 예산 동안의 반복수 -> iters/sec, realize 1회당 ms, 개선율.
                       (시간제한 메타휴리스틱의 해 품질은 '탐색한 이웃 수'에 비례하므로 핵심 지표)
  * 병목 함수        : cProfile self-time 상위 함수 (어디서 CPU를 쓰는지).
  * 해 품질          : feasible, Z1(지배)/Z2/Z3, objective, forced(강제 배치 = Z1의 주원인).

내구성 (노트북이 닫히거나 프로세스가 죽어도 끝난 결과는 보존):
  * 문제 1개가 끝날 때마다 결과 1줄을 JSONL에 append + flush + os.fsync (디스크 확정).
  * 보고서(.md)는 매 문제 후 temp 파일에 쓰고 os.replace 로 원자적 교체.
  * 재실행하면 이미 끝난 문제는 건너뛴다(이어서 진행). 한 문제가 예외로 죽어도 다음으로 계속.

백그라운드 실행 예 (터미널을 닫아도 계속):
  Windows : start /b python perf_report.py     (또는 pythonw perf_report.py)
  Git-Bash: nohup python perf_report.py &

===  설정: 아래 절대경로만 사용 환경에 맞게 고치면 된다  =========================
"""

import os
from pathlib import Path

# --- 절대경로 설정 (환경에 맞게 이 값들만 수정) --------------------------------
PROJECT_ROOT  = r"c:\Users\simon\OGC_2026\OGC_2026"                    # 프로젝트 루트 (Phase0..Outer, myalgorithm.py)
PROB_DIRS     = [                                                      # 인스턴스 폴더들 (prob_*.json)
    r"c:\Users\simon\OGC_2026\OGC_2026\train_1",
    r"c:\Users\simon\OGC_2026\OGC_2026\train",
]
BASELINE_DIR  = r"c:\Users\simon\OGC_2026\OGC_2026\ogc2026\baseline"   # utils.py(평가기) 폴백 위치
OUT_DIR       = r"c:\Users\simon\OGC_2026\OGC_2026\perf_out"           # 결과/보고서 출력 폴더

# Override the original author-specific paths with the current workspace.
_HERE = Path(__file__).resolve().parent
PROJECT_ROOT = str(_HERE)
PROB_DIRS = [str(_HERE / "train")] if (_HERE / "train").is_dir() else []
BASELINE_DIR = str(_HERE / "ogc2026" / "baseline")
OUT_DIR = str(_HERE / "perf_out")

ALNS_BUDGET_S = 8.0     # ALNS 처리량 측정용 문제당 예산(초). 늘리면 iters/s 추정이 안정적, 총 실행시간 증가.
TOP_FUNCS     = 12      # 병목 상위 함수 개수 (per-prob & aggregate)

# 실제 제출 경로(4-워커 포트폴리오)까지 돌려 '실전 objective/feasibility'를 측정할지.
# 기본 off = 진단만(빠름, ~문제당 15~25s). True로 켜면 문제당 REAL_TIMELIMIT초가 더 들지만,
# 실제 알고리즘이 내는 목적함수/feasible을 그대로 재므로 '알고리즘 성능 판단'이 완전해진다.
MEASURE_REAL_ALGORITHM = False
REAL_TIMELIMIT         = 60.0   # 실전 측정 시 문제당 timelimit(초). 진입점이 reserve로 ~25s를 떼고
                                # 워커 reap 마진도 빠지므로 실제 최적화 시간 ≈ (T-30)초.
                                # 60 미만이면 최적화가 거의 안 돼 construct와 비슷하게 나온다 -> 60 이상 권장.
# ==============================================================================

import sys
import re
import io
import json
import glob
import time
import pstats
import cProfile
import traceback
import datetime as _dt

# --- import 경로: 프로젝트 루트를 '맨 앞'에, baseline은 '맨 뒤'에 ----------------
# baseline/ 에도 myalgorithm.py(대회 참조 greedy)가 있어, baseline을 앞에 두면
# 'from myalgorithm import ...' 가 프로젝트가 아니라 baseline greedy를 잡는다.
# -> 프로젝트 루트를 sys.path[0]에, baseline(utils 폴백 전용)은 맨 뒤에 둔다.
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
if BASELINE_DIR not in sys.path:
    sys.path.append(BASELINE_DIR)

# Windows 콘솔(cp949) 한글 깨짐 방지 (출력 파일은 항상 UTF-8로 저장됨)
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import utils  # 평가기 (baseline/utils.py)

# 솔버 진입점들 (프로젝트)
from Phase0 import preprocess
from Phase1 import BuildBayAssignment
from Phase1.timing import init_timing
from Phase2 import PlaceAndCrane, Phase2Config
from Outer.realize import realize
from Outer.alns import alns
from Outer.portfolio import default_portfolio

_MEMBER0 = default_portfolio()[0]      # 기준선 config (ALNS 처리량 측정에 사용)


# ---------------------------------------------------------------------------
# 파일/durability 유틸
# ---------------------------------------------------------------------------

def _now():
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _natkey(path):
    m = re.findall(r"(\d+)", os.path.basename(path))
    return (int(m[-1]) if m else 0, os.path.basename(path))


def discover_probs():
    files = []
    for d in PROB_DIRS:
        files += glob.glob(os.path.join(d, "prob_*.json"))
    # prob_1, prob_2, ... 순 (trailing 정수 기준)
    return sorted(files, key=_natkey)


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
    done = {}
    if os.path.exists(jsonl_path):
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    r = json.loads(ln)
                    done[r["prob"]] = r
                except Exception:
                    pass
    return done


# ---------------------------------------------------------------------------
# 측정: 문제 하나
# ---------------------------------------------------------------------------

def _top_funcs_from_profile(pr, top_n):
    """cProfile.Profile -> self-time(tottime) 상위 함수 리스트 [(tottime, ncalls, label), ...]."""
    st = pstats.Stats(pr, stream=io.StringIO())
    rows = []
    for (fpath, line, name), (cc, nc, tt, ct, callers) in st.stats.items():
        label = "%s (%s:%d)" % (name, os.path.basename(fpath), line)
        rows.append((tt, nc, label))
    rows.sort(reverse=True)
    return [(round(tt, 4), nc, label) for (tt, nc, label) in rows[:top_n]]


def run_one(path):
    """문제 하나를 측정하고 dict 반환. 예외는 error 필드에 담아 항상 dict 반환."""
    key = prob_key(path)
    rec = {"prob": key, "ts": _now(), "error": None}
    t_all = time.perf_counter()
    try:
        with open(path, "r", encoding="utf-8") as f:
            prob = json.load(f)
        w = prob.get("weights", {})
        w1, w2, w3 = w.get("w1", 1.0), w.get("w2", 1.0), w.get("w3", 1.0)
        rec["n_blocks"] = len(prob["blocks"])
        rec["n_bays"] = len(prob["bays"])

        # ---- phase별 수행시간 (cold: NFP 캐시가 비어 있는 첫 실행) ----
        t = time.perf_counter(); pre = preprocess(prob);                    rec["t_phase0_pre"]   = time.perf_counter() - t
        t = time.perf_counter(); p1 = BuildBayAssignment(prob, pre, None);  rec["t_phase1_asgn"]  = time.perf_counter() - t
        bay = list(p1.bay)
        t = time.perf_counter(); p1out = init_timing(bay, prob, pre);       rec["t_phase1_timing"] = time.perf_counter() - t
        t = time.perf_counter(); res = PlaceAndCrane(prob, p1out, pre, Phase2Config())
        rec["t_phase2_place_cold"] = time.perf_counter() - t   # 첫 배치 = lazy NFP 빌드 포함(문제당 1회성 startup)

        # ---- 구성(construct) 해 품질 ----
        feasible = bool(res.info.get("feasible", False))
        Z1 = res.Z1 if (feasible and res.Z1 is not None) else None
        Z2, Z3 = p1out.Z2, p1out.Z3
        rec["construct_feasible"] = feasible
        rec["Z1"], rec["Z2"], rec["Z3"] = Z1, Z2, Z3
        rec["forced"] = len(res.info.get("forced", []))
        rec["construct_objective"] = (w1 * Z1 + w2 * Z2 + w3 * Z3) if feasible else None

        # 평가기(utils) 교차검증 (내부 판정과 서버 판정 일치 확인)
        chk = utils.check_feasibility(prob, res.solution)
        rec["utils_feasible"] = bool(chk["feasible"])
        rec["utils_objective"] = chk["objective"]

        # ---- 병목: warm realize 1회 cProfile (NFP 캐시는 위에서 채워짐) ----
        pr = cProfile.Profile()
        pr.enable()
        realize(bay, prob, pre, Phase2Config())
        pr.disable()
        rec["top_funcs"] = _top_funcs_from_profile(pr, max(TOP_FUNCS, 20))

        # ---- ALNS 처리량 (핵심): 고정 예산 동안 반복수/개선 ----
        s_best, stats = alns(prob, pre, ALNS_BUDGET_S, _MEMBER0)
        iters = stats.get("iters", 0)
        f0, fbest = stats.get("f0"), stats.get("f_best")
        rec["alns_budget_s"] = ALNS_BUDGET_S
        rec["alns_iters"] = iters
        rec["alns_iters_per_s"] = (iters / ALNS_BUDGET_S) if ALNS_BUDGET_S > 0 else 0.0
        rec["alns_ms_per_realize"] = (ALNS_BUDGET_S / iters * 1000.0) if iters else None
        rec["alns_f0"] = f0
        rec["alns_fbest"] = fbest
        rec["alns_improve_pct"] = ((f0 - fbest) / f0 * 100.0) if (f0 and fbest is not None and f0 > 0) else None
        rec["alns_feasible"] = bool(getattr(s_best, "feasible", False))

        # ---- (옵션) 실전 성능: 실제 제출 알고리즘(4-워커 포트폴리오)의 objective/feasible ----
        if MEASURE_REAL_ALGORITHM:
            from myalgorithm import algorithm   # 프로젝트 것(sys.path 루트 우선 -> baseline greedy 아님)
            t = time.perf_counter()
            sol = algorithm(prob, REAL_TIMELIMIT)
            rec["real_elapsed"] = round(time.perf_counter() - t, 2)
            rchk = utils.check_feasibility(prob, sol)
            rec["real_feasible"] = bool(rchk["feasible"])
            rec["real_objective"] = rchk["objective"]
            co, ro = rec.get("construct_objective"), rchk["objective"]
            rec["real_gain_vs_construct_pct"] = (
                (co - ro) / co * 100.0) if (co and ro is not None and rchk["feasible"] and co > 0) else None

    except Exception:
        rec["error"] = traceback.format_exc()
    rec["t_total_measure"] = round(time.perf_counter() - t_all, 3)
    return rec


# ---------------------------------------------------------------------------
# 보고서 생성 (매 문제 후 재생성 -> durable)
# ---------------------------------------------------------------------------

def _fmt(x, nd=3):
    if x is None:
        return "—"
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


def build_report(records, total, started):
    ok = [r for r in records if not r.get("error")]
    errs = [r for r in records if r.get("error")]
    L = []
    L.append("# OGC 2026 성능 분석 보고서")
    L.append("")
    L.append(f"- 시작 {started} · 갱신 {_now()}")
    L.append(f"- 진행 **{len(records)}/{total}** · 성공 {len(ok)} · 오류 {len(errs)} · ALNS 예산 {ALNS_BUDGET_S}s/문제")
    if not ok:
        L.append("\n(측정된 문제가 아직 없습니다.)")
        return "\n".join(L) + "\n"

    # ---- 1) 요약: phase 시간 / 처리량 / 품질 ----
    def col(k):
        return [r.get(k) for r in ok]
    startup = [(r.get("t_phase0_pre", 0) + r.get("t_phase1_asgn", 0)
                + r.get("t_phase1_timing", 0) + r.get("t_phase2_place_cold", 0)) for r in ok]
    L.append("\n## 1. 핵심 요약 (성공 문제 기준)")
    L.append("")
    L.append("| 지표 | 평균 | 중앙값 | 최소 | 최대 |")
    L.append("|---|---|---|---|---|")

    def line(name, xs, nd=3):
        xs2 = [v for v in xs if v is not None]
        if not xs2:
            L.append(f"| {name} | — | — | — | — |")
            return
        L.append(f"| {name} | {_fmt(_mean(xs2), nd)} | {_fmt(_median(xs2), nd)} | {_fmt(min(xs2), nd)} | {_fmt(max(xs2), nd)} |")

    line("Phase0 preprocess (s)", col("t_phase0_pre"))
    line("Phase1 배정 (s)", col("t_phase1_asgn"))
    line("Phase1 타이밍 (s)", col("t_phase1_timing"))
    line("Phase2 첫배치/cold NFP (s)", col("t_phase2_place_cold"))
    line("구성(startup) 합계 (s)", startup)
    line("ALNS iters/sec", col("alns_iters_per_s"), 1)
    line("ALNS ms/realize", col("alns_ms_per_realize"), 2)
    line("ALNS 개선율 (%)", col("alns_improve_pct"), 2)
    line("forced (강제배치 수)", col("forced"), 1)

    # ---- 2) 어디가 병목인가: startup 시간 phase 분해 ----
    tot_pre = sum(v or 0 for v in col("t_phase0_pre"))
    tot_asg = sum((r.get("t_phase1_asgn", 0) or 0) + (r.get("t_phase1_timing", 0) or 0) for r in ok)
    tot_p2 = sum(v or 0 for v in col("t_phase2_place_cold"))
    tot = tot_pre + tot_asg + tot_p2 or 1.0
    L.append("\n## 2. 구성(startup) 시간의 phase별 비중")
    L.append("")
    L.append(f"- Phase0 preprocess : **{tot_pre/tot*100:5.1f}%** ({tot_pre:.1f}s)")
    L.append(f"- Phase1 배정+타이밍 : **{tot_asg/tot*100:5.1f}%** ({tot_asg:.1f}s)")
    L.append(f"- Phase2 첫배치      : **{tot_p2/tot*100:5.1f}%** ({tot_p2:.1f}s)  ← 대개 lazy NFP 빌드가 지배(문제당 1회)")
    L.append("")
    L.append("> ALNS 반복 1회의 비용은 위 'cold' 가 아니라 **ms/realize**(warm)로 봐야 한다. "
             "시간제한 내 해 품질은 **iters/sec**(탐색한 이웃 수)에 직접 비례하므로, "
             "iters/sec 가 낮은 문제가 실질 병목이다.")

    # ---- 3) 병목 함수 (self-time 집계) ----
    agg = {}
    for r in ok:
        for (tt, nc, label) in (r.get("top_funcs") or []):
            agg[label] = agg.get(label, 0.0) + (tt or 0.0)
    top = sorted(agg.items(), key=lambda kv: kv[1], reverse=True)[:TOP_FUNCS]
    L.append("\n## 3. 병목 함수 (warm realize cProfile self-time, 전 문제 합)")
    L.append("")
    if top:
        L.append("| 함수 | 총 self-time (s) |")
        L.append("|---|---|")
        for label, tt in top:
            L.append(f"| `{label}` | {tt:.3f} |")
        L.append("\n> 여기 상위 함수가 realize hot loop의 실제 CPU 소비처 = 최적화하면 iters/sec 가 오른다.")

    # ---- 3.5) 실전 성능 (MEASURE_REAL_ALGORITHM=True 일 때만) ----
    real = [r for r in ok if r.get("real_objective") is not None or r.get("real_feasible") is not None]
    if real:
        rf = sum(1 for r in real if r.get("real_feasible"))
        gains = [r.get("real_gain_vs_construct_pct") for r in real]
        L.append("\n## 3.5 실전 성능 (실제 제출 알고리즘, 4-워커 포트폴리오)")
        L.append("")
        L.append(f"- timelimit {REAL_TIMELIMIT}s/문제 · feasible **{rf}/{len(real)}**")
        L.append(f"- construct 대비 목적함수 개선율: 평균 {_fmt(_mean(gains), 2)}% · 중앙값 {_fmt(_median(gains), 2)}%")
        L.append("")
        L.append("| prob | real feas | real objective | construct 대비 개선% | 실측(s) |")
        L.append("|---|---|---|---|---|")
        for r in sorted(real, key=lambda r: _natkey(r["prob"])):
            L.append("| {p} | {fe} | {o} | {g} | {t} |".format(
                p=r["prob"], fe="Y" if r.get("real_feasible") else "N",
                o=_fmt(r.get("real_objective"), 0), g=_fmt(r.get("real_gain_vs_construct_pct"), 2),
                t=_fmt(r.get("real_elapsed"), 1)))

    # ---- 4) 상관: 문제규모 vs 처리량, forced vs Z1 ----
    L.append("\n## 4. 관계 분석")
    L.append("")
    nb = [(r.get("n_blocks"), r.get("alns_iters_per_s")) for r in ok
          if r.get("n_blocks") and r.get("alns_iters_per_s")]
    if nb:
        slow = sorted(nb, key=lambda p: p[1])[:3]
        L.append("- 처리량 최저(=병목) 문제 상위3 (n_blocks, iters/s): "
                 + ", ".join(f"({b}, {ips:.1f})" for b, ips in slow))
    fz = [(r.get("forced"), r.get("Z1")) for r in ok if r.get("forced") is not None and r.get("Z1") is not None]
    if fz:
        # 단순 피어슨 상관
        xs = [a for a, _ in fz]; ys = [b for _, b in fz]
        mx, my = _mean(xs), _mean(ys)
        num = sum((a - mx) * (b - my) for a, b in fz)
        den = (sum((a - mx) ** 2 for a in xs) * sum((b - my) ** 2 for b in ys)) ** 0.5
        corr = (num / den) if den else 0.0
        L.append(f"- forced(강제배치) ↔ Z1(지연) 상관계수: **{corr:+.2f}** "
                 f"(강제배치가 Z1의 주원인인지 정량 확인; +1에 가까울수록 강제배치 줄이기가 최우선 레버)")

    # ---- 5) 전 문제 표 ----
    L.append("\n## 5. 문제별 상세")
    L.append("")
    L.append("| prob | n_blk | P0(s) | P1(s) | P2cold(s) | iters/s | ms/real | 개선% | forced | Z1 | objective | feas |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in sorted(records, key=lambda r: _natkey(r["prob"])):
        if r.get("error"):
            L.append(f"| {r['prob']} | ERR | | | | | | | | | | |")
            continue
        p1t = (r.get("t_phase1_asgn", 0) or 0) + (r.get("t_phase1_timing", 0) or 0)
        L.append("| {p} | {nb} | {p0} | {p1} | {p2} | {ips} | {msr} | {imp} | {frc} | {z1} | {obj} | {fe} |".format(
            p=r["prob"], nb=r.get("n_blocks", ""),
            p0=_fmt(r.get("t_phase0_pre"), 3), p1=_fmt(p1t, 3), p2=_fmt(r.get("t_phase2_place_cold"), 3),
            ips=_fmt(r.get("alns_iters_per_s"), 1), msr=_fmt(r.get("alns_ms_per_realize"), 1),
            imp=_fmt(r.get("alns_improve_pct"), 2), frc=r.get("forced", ""),
            z1=_fmt(r.get("Z1"), 0), obj=_fmt(r.get("construct_objective"), 0),
            fe="Y" if r.get("utils_feasible") else "N"))

    if errs:
        L.append("\n## 오류 문제")
        for r in errs:
            head = (r.get("error") or "").strip().splitlines()[-1:] or [""]
            L.append(f"- {r['prob']}: {head[0]}")
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------

def _warmup_jit(probs):
    """numba JIT + 임포트 워밍업 1회 (측정 전에 컴파일을 끝내 첫 문제 수치 오염 방지)."""
    for p in probs:
        try:
            with open(p, "r", encoding="utf-8") as f:
                prob = json.load(f)
            pre = preprocess(prob)
            p1 = BuildBayAssignment(prob, pre, None)
            realize(list(p1.bay), prob, pre, Phase2Config())
            return
        except Exception:
            continue


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    jsonl_path = os.path.join(OUT_DIR, "perf_results.jsonl")
    report_path = os.path.join(OUT_DIR, "perf_report_setLastObj.md")
    log_path = os.path.join(OUT_DIR, "perf_run.log")

    def log(msg):
        line = f"[{_now()}] {msg}"
        print(line, flush=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n"); f.flush()

    probs = discover_probs()
    done = load_done(jsonl_path)
    records = list(done.values())
    started = _now()

    log(f"START probs={len(probs)} resume={len(done)} out={OUT_DIR} alns_budget={ALNS_BUDGET_S}s")
    if not probs:
        log("경고: 문제 파일을 찾지 못했습니다. PROB_DIRS 절대경로를 확인하세요.")
        return

    log("numba JIT 워밍업 중...")
    _warmup_jit(probs)
    log("워밍업 완료. 측정 시작.")

    for path in probs:
        key = prob_key(path)
        if key in done:
            continue
        rec = run_one(path)
        fsync_append(jsonl_path, json.dumps(rec, ensure_ascii=False))   # 결과 즉시 디스크 확정
        records.append(rec)
        atomic_write(report_path, build_report(records, len(probs), started))  # 보고서 원자적 갱신
        if rec.get("error"):
            log(f"  {key:16s} ERROR (측정 {rec.get('t_total_measure')}s) — 기록 후 계속")
        else:
            log("  {k:16s} feas={fe} iters/s={ips} ms/real={ms} forced={f} obj={o} ({t}s)".format(
                k=key, fe=rec.get("utils_feasible"),
                ips=_fmt(rec.get("alns_iters_per_s"), 1), ms=_fmt(rec.get("alns_ms_per_realize"), 1),
                f=rec.get("forced"), o=_fmt(rec.get("construct_objective"), 0), t=rec.get("t_total_measure")))

    log(f"DONE {len(records)}/{len(probs)}  보고서: {report_path}")


if __name__ == "__main__":
    main()
