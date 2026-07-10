#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""
perf_report.py -- OGC 2026 performance report harness.

Runs per-problem construction timing, a warm realize profile, and an ALNS
experiment. By default this uses deadline-based ALNS so the harness exercises
the new solver path directly.
"""

import os
from pathlib import Path

PROJECT_ROOT = r"c:\Users\simon\OGC_2026\OGC_2026"
PROB_DIRS = [
    r"c:\Users\simon\OGC_2026\OGC_2026\train_1",
    r"c:\Users\simon\OGC_2026\OGC_2026\train",
]
BASELINE_DIR = r"c:\Users\simon\OGC_2026\OGC_2026\ogc2026\baseline"
OUT_DIR = r"c:\Users\simon\OGC_2026\OGC_2026\perf_out"

_HERE = Path(__file__).resolve().parent
PROJECT_ROOT = str(_HERE)
PROB_DIRS = [str(_HERE / "train")] if (_HERE / "train").is_dir() else []
BASELINE_DIR = str(_HERE / "ogc2026" / "baseline")
OUT_DIR = str(_HERE / "perf_out")

ALNS_DEADLINE_S = 53.0  # FIXME
ALNS_BUDGET_S = 3.0    # Fixed-budget fallback. Set ALNS_DEADLINE_S = None to use this mode.
TOP_FUNCS = 12

MEASURE_REAL_ALGORITHM = False
REAL_TIMELIMIT = 60.0

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

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
if BASELINE_DIR not in sys.path:
    sys.path.append(BASELINE_DIR)

try:
    from myalgorithm import ALNS_DEADLINE_S as _SOLVER_ALNS_DEADLINE_S
    ALNS_DEADLINE_S = _SOLVER_ALNS_DEADLINE_S
except Exception:
    pass

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import utils

from Phase0 import preprocess
from Phase1 import BuildBayAssignment
from Phase1.timing import init_timing
from Phase2 import PlaceAndCrane, Phase2Config
from Outer.alns import alns
from Outer.portfolio import default_portfolio
from Outer.realize import realize

_MEMBER0 = default_portfolio()[0]


def _now():
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _natkey(path):
    m = re.findall(r"(\d+)", os.path.basename(path))
    return (int(m[-1]) if m else 0, os.path.basename(path))


def _alns_mode_label():
    if ALNS_DEADLINE_S is not None:
        return f"alns_deadline={ALNS_DEADLINE_S}s"
    return f"alns_budget={ALNS_BUDGET_S}s"


def discover_probs():
    files = []
    for d in PROB_DIRS:
        files += glob.glob(os.path.join(d, "prob_*.json"))
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


def _has_final_alns_metrics(rec):
    return (
        "alns_utils_objective" in rec
        and "alns_utils_Z1" in rec
        and "alns_best_forced" in rec
        and "alns_elapsed_s" in rec
        and "alns_stopped_by_deadline" in rec
        and rec.get("alns_deadline_scope") == "solver_start"
    )


def _top_funcs_from_profile(pr, top_n):
    st = pstats.Stats(pr, stream=io.StringIO())
    rows = []
    for (fpath, line, name), (cc, nc, tt, ct, callers) in st.stats.items():
        label = "%s (%s:%d)" % (name, os.path.basename(fpath), line)
        rows.append((tt, nc, label))
    rows.sort(reverse=True)
    return [(round(tt, 4), nc, label) for (tt, nc, label) in rows[:top_n]]


def _run_alns_measure(prob, pre, deadline=None):
    alns_start = time.perf_counter()
    if deadline is not None:
        s_best, stats = alns(
            prob,
            pre,
            budget_s=None,
            cfg=_MEMBER0,
            deadline=deadline,
            deadline_s=ALNS_DEADLINE_S,
        )
        mode_fields = {
            "alns_deadline_s": ALNS_DEADLINE_S,
            "alns_budget_s": None,
        }
    else:
        s_best, stats = alns(prob, pre, budget_s=ALNS_BUDGET_S, cfg=_MEMBER0)
        mode_fields = {
            "alns_deadline_s": None,
            "alns_budget_s": ALNS_BUDGET_S,
        }
    measured_elapsed = time.perf_counter() - alns_start
    elapsed = stats.get("elapsed_s", measured_elapsed)
    return s_best, stats, elapsed, mode_fields


def run_one(path):
    key = prob_key(path)
    rec = {"prob": key, "ts": _now(), "error": None}
    t_all = time.perf_counter()
    solver_start = t_all
    solver_deadline = (solver_start + ALNS_DEADLINE_S) if ALNS_DEADLINE_S is not None else None
    try:
        with open(path, "r", encoding="utf-8") as f:
            prob = json.load(f)
        w = prob.get("weights", {})
        w1, w2, w3 = w.get("w1", 1.0), w.get("w2", 1.0), w.get("w3", 1.0)
        rec["n_blocks"] = len(prob["blocks"])
        rec["n_bays"] = len(prob["bays"])

        t = time.perf_counter()
        pre = preprocess(prob)
        rec["t_phase0_pre"] = time.perf_counter() - t

        t = time.perf_counter()
        p1 = BuildBayAssignment(prob, pre, None)
        rec["t_phase1_asgn"] = time.perf_counter() - t
        bay = list(p1.bay)

        t = time.perf_counter()
        p1out = init_timing(bay, prob, pre)
        rec["t_phase1_timing"] = time.perf_counter() - t

        t = time.perf_counter()
        res = PlaceAndCrane(prob, p1out, pre, Phase2Config())
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

        s_best, stats, elapsed, mode_fields = _run_alns_measure(prob, pre, deadline=solver_deadline)
        rec.update(mode_fields)
        iters = stats.get("iters", 0)
        f0 = stats.get("f0")
        fbest = stats.get("f_best")
        rec["alns_elapsed_s"] = elapsed
        rec["alns_deadline_scope"] = "solver_start"
        rec["alns_total_to_end_s"] = time.perf_counter() - solver_start
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
        rec["alns_best_solution_returned"] = bool(getattr(s_best, "solution", None) is not None)

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
            rec["alns_utils_Z1"] = None
            rec["alns_utils_Z2"] = None
            rec["alns_utils_Z3"] = None

        # Profile warm realize after the ALNS experiment so diagnostics do not
        # consume the solver's global deadline budget.
        pr = cProfile.Profile()
        pr.enable()
        realize(bay, prob, pre, Phase2Config())
        pr.disable()
        rec["top_funcs"] = _top_funcs_from_profile(pr, max(TOP_FUNCS, 20))

        if MEASURE_REAL_ALGORITHM:
            from myalgorithm import algorithm

            t = time.perf_counter()
            sol = algorithm(prob, REAL_TIMELIMIT)
            rec["real_elapsed"] = round(time.perf_counter() - t, 2)
            rchk = utils.check_feasibility(prob, sol)
            rec["real_feasible"] = bool(rchk["feasible"])
            rec["real_objective"] = rchk["objective"]
            co, ro = rec.get("construct_objective"), rchk["objective"]
            rec["real_gain_vs_construct_pct"] = (
                (co - ro) / co * 100.0
            ) if (co and ro is not None and rchk["feasible"] and co > 0) else None

    except Exception:
        rec["error"] = traceback.format_exc()
    rec["t_total_measure"] = round(time.perf_counter() - t_all, 3)
    return rec


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


def build_report(records, total, started):
    ok = [r for r in records if not r.get("error")]
    errs = [r for r in records if r.get("error")]
    lines = []
    lines.append("# OGC 2026 성능 분석 보고서")
    lines.append("")
    lines.append(f"- 시작 {started} · 갱신 {_now()}")
    lines.append(f"- 진행 **{len(records)}/{total}** · 성공 {len(ok)} · 오류 {len(errs)} · {_alns_mode_label()}")
    if not ok:
        lines.append("\n(측정된 문제가 아직 없습니다.)")
        return "\n".join(lines) + "\n"

    def col(k):
        return [r.get(k) for r in ok]

    startup = [
        (r.get("t_phase0_pre", 0) + r.get("t_phase1_asgn", 0) +
         r.get("t_phase1_timing", 0) + r.get("t_phase2_place_cold", 0))
        for r in ok
    ]

    lines.append("\n## 1. 진단 요약 (성공 문제 기준)")
    lines.append("")
    lines.append("| 지표 | 평균 | 중앙값 | 최소 | 최대 |")
    lines.append("|---|---|---|---|---|")

    def line(name, xs, nd=3):
        xs2 = [v for v in xs if v is not None]
        if not xs2:
            lines.append(f"| {name} | NA | NA | NA | NA |")
            return
        lines.append(
            f"| {name} | {_fmt(_mean(xs2), nd)} | {_fmt(_median(xs2), nd)} | "
            f"{_fmt(min(xs2), nd)} | {_fmt(max(xs2), nd)} |"
        )

    line("Phase0 preprocess (s)", col("t_phase0_pre"))
    line("Phase1 assignment (s)", col("t_phase1_asgn"))
    line("Phase1 timing (s)", col("t_phase1_timing"))
    line("Phase2 first place cold NFP (s)", col("t_phase2_place_cold"))
    line("Construction startup total (s)", startup)
    line("ALNS elapsed (s)", col("alns_elapsed_s"), 2)
    line("ALNS iters/sec", col("alns_iters_per_s"), 2)
    line("ALNS ms/realize", col("alns_ms_per_realize"), 2)
    line("ALNS improve (%)", col("alns_improve_pct"), 2)
    line("forced (ALNS best)", col("alns_best_forced"), 1)

    tot_pre = sum(v or 0 for v in col("t_phase0_pre"))
    tot_asg = sum((r.get("t_phase1_asgn", 0) or 0) + (r.get("t_phase1_timing", 0) or 0) for r in ok)
    tot_p2 = sum(v or 0 for v in col("t_phase2_place_cold"))
    tot = tot_pre + tot_asg + tot_p2 or 1.0
    lines.append("\n## 2. Construction startup phase breakdown")
    lines.append("")
    lines.append(f"- Phase0 preprocess : **{tot_pre / tot * 100:5.1f}%** ({tot_pre:.1f}s)")
    lines.append(f"- Phase1 assignment+timing : **{tot_asg / tot * 100:5.1f}%** ({tot_asg:.1f}s)")
    lines.append(f"- Phase2 first placement : **{tot_p2 / tot * 100:5.1f}%** ({tot_p2:.1f}s)")
    lines.append("")
    lines.append("> For ALNS throughput, use actual `elapsed_s` and warm `ms/realize`, not the cold startup placement cost.")

    agg = {}
    for r in ok:
        for (tt, nc, label) in (r.get("top_funcs") or []):
            agg[label] = agg.get(label, 0.0) + (tt or 0.0)
    top = sorted(agg.items(), key=lambda kv: kv[1], reverse=True)[:TOP_FUNCS]
    lines.append("\n## 3. Hot functions (warm realize cProfile self-time)")
    lines.append("")
    if top:
        lines.append("| Function | Total self-time (s) |")
        lines.append("|---|---|")
        for label, tt in top:
            lines.append(f"| `{label}` | {tt:.3f} |")

    real = [r for r in ok if r.get("real_objective") is not None or r.get("real_feasible") is not None]
    if real:
        rf = sum(1 for r in real if r.get("real_feasible"))
        gains = [r.get("real_gain_vs_construct_pct") for r in real]
        lines.append("\n## 3.5 Real algorithm")
        lines.append("")
        lines.append(f"- timelimit {REAL_TIMELIMIT}s/problem · feasible **{rf}/{len(real)}**")
        lines.append(
            f"- gain vs construction mean {_fmt(_mean(gains), 2)}% · median {_fmt(_median(gains), 2)}%"
        )
        lines.append("")
        lines.append("| prob | real feas | real objective | gain vs construct % | elapsed(s) |")
        lines.append("|---|---|---|---|---|")
        for r in sorted(real, key=lambda rec: _natkey(rec["prob"])):
            lines.append("| {p} | {fe} | {o} | {g} | {t} |".format(
                p=r["prob"],
                fe="Y" if r.get("real_feasible") else "N",
                o=_fmt(r.get("real_objective"), 0),
                g=_fmt(r.get("real_gain_vs_construct_pct"), 2),
                t=_fmt(r.get("real_elapsed"), 1),
            ))

    lines.append("\n## 4. Correlations")
    lines.append("")
    nb = [
        (r.get("n_blocks"), r.get("alns_iters_per_s"))
        for r in ok
        if r.get("n_blocks") and r.get("alns_iters_per_s") is not None
    ]
    if nb:
        slow = sorted(nb, key=lambda p: p[1])[:3]
        lines.append("- Slowest throughput problems top-3 (n_blocks, iters/s): " +
                     ", ".join(f"({b}, {ips:.2f})" for b, ips in slow))
    fz = [
        (r.get("alns_best_forced"), r.get("alns_utils_Z1"))
        for r in ok
        if r.get("alns_best_forced") is not None and r.get("alns_utils_Z1") is not None
    ]
    if fz:
        xs = [a for a, _ in fz]
        ys = [b for _, b in fz]
        mx, my = _mean(xs), _mean(ys)
        num = sum((a - mx) * (b - my) for a, b in fz)
        den = (sum((a - mx) ** 2 for a in xs) * sum((b - my) ** 2 for b in ys)) ** 0.5
        corr = (num / den) if den else 0.0
        lines.append(f"- forced vs Z1 correlation: **{corr:+.2f}**")

    lines.append("\n## 5. Per-problem detail")
    lines.append("")
    lines.append("| prob | n_blk | P0(s) | P1(s) | P2cold(s) | ALNS(s) | iters | iters/s | ms/real | improve% | stop_deadline | forced | Z1 | objective | feas |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in sorted(records, key=lambda rec: _natkey(rec["prob"])):
        if r.get("error"):
            lines.append(f"| {r['prob']} | ERR | | | | | | | | | | | | | |")
            continue
        p1t = (r.get("t_phase1_asgn", 0) or 0) + (r.get("t_phase1_timing", 0) or 0)
        lines.append("| {p} | {nb} | {p0} | {p1} | {p2} | {ae} | {it} | {ips} | {msr} | {imp} | {sd} | {frc} | {z1} | {obj} | {fe} |".format(
            p=r["prob"],
            nb=r.get("n_blocks", ""),
            p0=_fmt(r.get("t_phase0_pre"), 3),
            p1=_fmt(p1t, 3),
            p2=_fmt(r.get("t_phase2_place_cold"), 3),
            ae=_fmt(r.get("alns_elapsed_s"), 2),
            it=r.get("alns_iters", ""),
            ips=_fmt(r.get("alns_iters_per_s"), 2),
            msr=_fmt(r.get("alns_ms_per_realize"), 1),
            imp=_fmt(r.get("alns_improve_pct"), 2),
            sd=r.get("alns_stopped_by_deadline"),
            frc=r.get("alns_best_forced", ""),
            z1=_fmt(r.get("alns_utils_Z1"), 0),
            obj=_fmt(r.get("alns_utils_objective"), 0),
            fe="Y" if r.get("alns_utils_feasible") else "N",
        ))

    if errs:
        lines.append("\n## Errors")
        for r in errs:
            head = (r.get("error") or "").strip().splitlines()[-1:] or [""]
            lines.append(f"- {r['prob']}: {head[0]}")
    return "\n".join(lines) + "\n"


def _warmup_jit(probs):
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
    report_path = os.path.join(OUT_DIR, "perf_report_set55sTimer.md")
    log_path = os.path.join(OUT_DIR, "perf_run.log")

    def log(msg):
        line = f"[{_now()}] {msg}"
        print(line, flush=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()

    probs = discover_probs()
    done = load_done(jsonl_path)
    stale = {k for k, r in done.items() if not _has_final_alns_metrics(r)}
    records = [r for k, r in done.items() if k not in stale]
    started = _now()

    log(f"START probs={len(probs)} resume={len(done)} stale={len(stale)} out={OUT_DIR} {_alns_mode_label()}")
    if not probs:
        log("경고: 문제 파일을 찾지 못했습니다. PROB_DIRS 절대경로를 확인하세요.")
        return

    log("numba JIT warmup...")
    _warmup_jit(probs)
    log("warmup done. measurement start.")

    for path in probs:
        key = prob_key(path)
        if key in done and key not in stale:
            continue
        rec = run_one(path)
        fsync_append(jsonl_path, json.dumps(rec, ensure_ascii=False))
        records.append(rec)
        atomic_write(report_path, build_report(records, len(probs), started))
        if rec.get("error"):
            log(f"  {key:16s} ERROR (measured {rec.get('t_total_measure')}s) -- recorded and continuing")
        else:
            log(
                "  {k:16s} feas={fe} elapsed={ea}s solve_to_alns_end={sa}s iters={it} "
                "iters/s={ips} ms/real={ms} stop_deadline={sd} forced={f} obj={o} ({t}s)".format(
                    k=key,
                    fe=rec.get("alns_utils_feasible"),
                    ea=_fmt(rec.get("alns_elapsed_s"), 1),
                    sa=_fmt(rec.get("alns_total_to_end_s"), 1),
                    it=rec.get("alns_iters"),
                    ips=_fmt(rec.get("alns_iters_per_s"), 2),
                    ms=_fmt(rec.get("alns_ms_per_realize"), 1),
                    sd=rec.get("alns_stopped_by_deadline"),
                    f=rec.get("alns_best_forced"),
                    o=_fmt(rec.get("alns_utils_objective"), 0),
                    t=rec.get("t_total_measure"),
                )
            )

    log(f"DONE {len(records)}/{len(probs)} report={report_path}")


if __name__ == "__main__":
    main()
