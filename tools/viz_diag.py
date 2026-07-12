#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""
tools/viz_diag.py -- forced-placement 진단 시각화 (REPLAY MVP, 솔버 무수정).

perf_out 병목/forced 이슈를 블록 단위로 확인. prob JSON -> preprocess + Phase1 +
PlaceAndCrane(단일 construction 디코드) -> info["forced"] + 블록별 배치/타이밍 -> 4개 PNG.
ALNS-best가 아니라 construction 해를 그린다(forced 발생 지점이자 결정적·빠름).

사용: python tools/viz_diag.py train/prob_27 [--out perf_out/diag/prob_27]
"""

import os
import sys
import json
import time
import argparse
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_BASELINE = _ROOT / "ogc2026" / "baseline"
if str(_BASELINE) not in sys.path:
    sys.path.append(str(_BASELINE))

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch
    import numpy as np
    _HAVE_MPL = True
except Exception:
    _HAVE_MPL = False

from Phase0 import preprocess
from Phase1 import BuildBayAssignment
from Phase1.timing import init_timing
from Phase2 import PlaceAndCrane, Phase2Config

# 색상(perf_report 관례: slate/tailwind hex, 영문 라벨)
C_DARK = "#0f172a"
C_BLUE = "#3b82f6"
C_RED = "#dc2626"
C_GRAY = "#94a3b8"
C_AMBER = "#d97706"
_BAY_HUES = [210, 35, 130, 280, 10, 170, 310, 60]   # gantt_widget 관례


# --------------------------------------------------------------- 해 생성/재구성

def solve_construction(prob_path, geom_mode=None):
    """construction 디코드 1회. (prob, pre, recs, meta) 반환."""
    with open(prob_path, "r", encoding="utf-8") as f:
        prob = json.load(f)
    t0 = time.perf_counter()
    pre = preprocess(prob, geom_mode=geom_mode)
    t_pre = time.perf_counter() - t0

    p1 = BuildBayAssignment(prob, pre, None)
    bay = list(p1.bay)
    p1out = init_timing(bay, prob, pre)

    t1 = time.perf_counter()
    res = PlaceAndCrane(prob, p1out, pre, Phase2Config())
    t_place = time.perf_counter() - t1

    n = len(prob["blocks"])
    forced_ids = set(res.info.get("forced", []))
    blocks = prob["blocks"]
    bays = prob["bays"]

    # 블록별 레코드
    recs = []
    for i in range(n):
        o = res.orient.get(i)
        area = pre.area[i][o] if o is not None else 0.0
        e, x = res.entry[i], res.exit_[i]
        due = blocks[i]["due_date"]
        rel = blocks[i]["release_time"]
        proc = blocks[i]["processing_time"]
        recs.append({
            "id": i, "bay": bay[i], "entry": e, "exit": x,
            "due": due, "release": rel, "proc": proc,
            "orient": o, "area": area,
            "tardiness": max(0, x - due),
            "delay_from_release": e - rel,
            "forced_phaseB": i in forced_ids,
        })

    # 점유율(bay j, 시각 t) = 그 시각 resident 면적합 / bay 면적
    W = [b["width"] for b in bays]
    H = [b["height"] for b in bays]
    bay_area = [W[j] * H[j] for j in range(len(bays))]
    by_bay = {}
    for r in recs:
        by_bay.setdefault(r["bay"], []).append(r)

    def occ(j, t):
        if j not in by_bay or bay_area[j] <= 0:
            return 0.0
        s = sum(r["area"] for r in by_bay[j] if r["entry"] <= t < r["exit"])
        return s / bay_area[j]

    # 파생: 시각별 점유율(원하던 시점=release, 실제 배치=entry) + 고립 여부
    for r in recs:
        j = r["bay"]
        mates = [k for k in by_bay[j] if k["id"] != r["id"]
                 and k["entry"] < r["exit"] and r["entry"] < k["exit"]]
        r["isolated"] = (len(mates) == 0)          # 창 내내 혼자 = 이슈 병리
        r["n_mates"] = len(mates)
        r["occ_at_release"] = occ(j, r["release"])  # 들어가고 싶던 시점 점유율
        r["occ_at_entry"] = occ(j, r["entry"])      # 실제 놓인 시점 점유율

    # 블록 유형: 면적 3분위(S/M/L), slack 3분위(tight/med/loose)
    areas = sorted(r["area"] for r in recs)
    slacks = sorted(pre.slack[i] for i in range(n))

    def tercile(v, arr, labels):
        if len(arr) < 3:
            return labels[1]
        lo, hi = arr[len(arr) // 3], arr[2 * len(arr) // 3]
        return labels[0] if v <= lo else (labels[2] if v > hi else labels[1])

    for r in recs:
        r["size_class"] = tercile(r["area"], areas, ["S", "M", "L"])
        r["slack_class"] = tercile(pre.slack[r["id"]], slacks, ["tight", "med", "loose"])

    horizon = max((r["exit"] for r in recs), default=1)
    meta = {
        "prob": os.path.splitext(os.path.basename(prob_path))[0],
        "n_blocks": n, "n_bays": len(bays), "W": W, "H": H,
        "feasible": bool(res.info.get("feasible", False)),
        "Z1": res.Z1, "forced_count": len(forced_ids),
        "isolated_count": sum(1 for r in recs if r["isolated"]),
        "tardy_count": sum(1 for r in recs if r["tardiness"] > 0),
        "horizon": horizon, "t_pre_s": round(t_pre, 2), "t_place_s": round(t_place, 2),
        "weights": prob.get("weights", {}),
    }
    return prob, pre, recs, meta


# ------------------------------------------------------------------------ 플롯

def _bay_color(j):
    h = _BAY_HUES[j % len(_BAY_HUES)] / 360.0
    import colorsys
    r, g, b = colorsys.hsv_to_rgb(h, 0.45, 0.85)
    return (r, g, b)


def plot_gantt(recs, meta, out):
    """패널1: bay별 타임라인. forced=굵은 빨강 테두리 'F', 고립=주황, tardiness=빨간 해칭."""
    fig, ax = plt.subplots(figsize=(12, 8))
    by_bay = {}
    for r in recs:
        by_bay.setdefault(r["bay"], []).append(r)
    row = 0
    yticks, ylabels, sep = [], [], []
    MAXROWS = 150
    total_rows = sum(len(v) for v in by_bay.values())
    truncated = total_rows > MAXROWS
    budget = MAXROWS
    for j in sorted(by_bay):
        blocks = sorted(by_bay[j], key=lambda r: r["entry"])
        if truncated:
            # forced/고립/지연 큰 블록 우선 보존
            blocks.sort(key=lambda r: (not r["forced_phaseB"], not r["isolated"],
                                       -r["tardiness"], r["entry"]))
            keep = max(1, int(budget * len(by_bay[j]) / total_rows))
            blocks = sorted(blocks[:keep], key=lambda r: r["entry"])
        sep.append(row - 0.5)
        for r in blocks:
            col = _bay_color(j)
            ax.barh(row, r["exit"] - r["entry"], left=r["entry"], height=0.8,
                    color=col, alpha=0.85, zorder=2,
                    edgecolor=(C_RED if r["forced_phaseB"] else
                               (C_AMBER if r["isolated"] else "none")),
                    linewidth=(2.2 if r["forced_phaseB"] else (1.6 if r["isolated"] else 0)))
            if r["tardiness"] > 0:                      # 지연분 빨간 해칭
                ax.barh(row, r["exit"] - r["due"], left=r["due"], height=0.8,
                        color="none", edgecolor=C_RED, hatch="////", linewidth=0, zorder=3)
            ax.plot(r["due"], row, marker="v", color=C_DARK, ms=4, zorder=4)   # due
            if r["forced_phaseB"] or r["isolated"]:
                ax.text(r["exit"] + 0.5, row, "F" if r["forced_phaseB"] else "i",
                        va="center", fontsize=6, color=C_RED if r["forced_phaseB"] else C_AMBER)
            row += 1
        yticks.append(row - len(blocks) / 2)
        ylabels.append(f"bay {j}")
    for s in sep[1:]:
        ax.axhline(s, color=C_GRAY, lw=0.6, alpha=0.5)
    ax.set_yticks(yticks)
    ax.set_yticklabels(ylabels)
    ax.set_xlabel("time (day)")
    ax.set_ylim(-1, row)
    ttl = f"{meta['prob']}  bay timeline  (forced={meta['forced_count']}, isolated={meta['isolated_count']}, tardy={meta['tardy_count']})"
    if truncated:
        ttl += "  [rows truncated, forced/isolated kept]"
    ax.set_title(ttl)
    ax.legend(handles=[
        Patch(facecolor="none", edgecolor=C_RED, lw=2.2, label="forced (Phase B)"),
        Patch(facecolor="none", edgecolor=C_AMBER, lw=1.6, label="isolated (alone in bay)"),
        Patch(facecolor="none", edgecolor=C_RED, hatch="////", label="tardiness (exit>due)"),
    ], loc="lower right", fontsize=8)
    ax.grid(True, axis="x", alpha=0.25)
    fig.tight_layout()
    fig.savefig(out, dpi=110)
    plt.close(fig)


def plot_occupancy_heatmap(recs, meta, out):
    """패널2: (일자 x bay) 점유율 히트맵 + forced/고립 블록의 release->entry 화살표(지연)."""
    horizon = int(meta["horizon"]) + 1
    nb = meta["n_bays"]
    nbins = min(horizon, 400)
    edges = np.linspace(0, horizon, nbins + 1)
    W, H = meta["W"], meta["H"]
    grid = np.zeros((nb, nbins))
    by_bay = {}
    for r in recs:
        by_bay.setdefault(r["bay"], []).append(r)
    for j in range(nb):
        ba = W[j] * H[j] if j < len(W) else 1
        for bi in range(nbins):
            t = 0.5 * (edges[bi] + edges[bi + 1])
            s = sum(r["area"] for r in by_bay.get(j, []) if r["entry"] <= t < r["exit"])
            grid[j, bi] = s / ba if ba > 0 else 0.0
    fig, ax = plt.subplots(figsize=(12, max(3, 0.7 * nb + 2)))
    im = ax.imshow(grid, aspect="auto", origin="lower", cmap="magma_r",
                   vmin=0, vmax=1, extent=[0, horizon, -0.5, nb - 0.5], interpolation="nearest")
    fig.colorbar(im, ax=ax, label="bay occupancy (area fraction)", shrink=0.8)
    # forced/고립 화살표
    marked = [r for r in recs if r["forced_phaseB"] or r["isolated"]]
    for r in marked:
        j = r["bay"]
        c = C_RED if r["forced_phaseB"] else C_AMBER
        ax.annotate("", xy=(r["entry"], j), xytext=(r["release"], j),
                    arrowprops=dict(arrowstyle="->", color=c, lw=0.8, alpha=0.8))
        ax.plot(r["release"], j, "o", color=C_BLUE, ms=3, zorder=5)   # 원하던 시점
        ax.plot(r["entry"], j, "s", color=c, ms=3, zorder=5)          # 실제 배치
    ax.set_yticks(range(nb))
    ax.set_yticklabels([f"bay {j}" for j in range(nb)])
    ax.set_xlabel("time (day)")
    ax.set_title(f"{meta['prob']}  occupancy heatmap  (blue o = wanted@release, square = landed@entry, arrow = forcing delay)")
    ax.legend(handles=[
        Patch(facecolor=C_BLUE, label="wanted (release time)"),
        Patch(facecolor=C_RED, label="forced landing"),
        Patch(facecolor=C_AMBER, label="isolated landing"),
    ], loc="upper right", fontsize=8)
    fig.tight_layout()
    fig.savefig(out, dpi=110)
    plt.close(fig)


def plot_forced_anatomy(recs, meta, out):
    """패널3(핵심 진단): x=release 시점 bay 점유율, y=지연(entry-release, log), 색=size class.
    저점유=자리 있는데 forced(파편화), 고점유=진짜 혼잡."""
    marked = [r for r in recs if (r["forced_phaseB"] or r["isolated"]) and r["delay_from_release"] > 0]
    if not marked:
        return False
    fig, ax = plt.subplots(figsize=(9.5, 6))
    col = {"S": C_BLUE, "M": C_AMBER, "L": C_RED}
    for sc in ["S", "M", "L"]:
        pts = [r for r in marked if r["size_class"] == sc]
        if pts:
            ax.scatter([r["occ_at_release"] * 100 for r in pts],
                       [max(1, r["delay_from_release"]) for r in pts],
                       s=[30 + 90 * (r["proc"] / max(1, meta["horizon"])) for r in pts],
                       c=col[sc], alpha=0.6, edgecolor="white", linewidth=0.4,
                       label=f"size {sc} (n={len(pts)})", zorder=3)
    ax.axvline(85, color=C_GRAY, ls=":", lw=1, label="85% (crowded)")
    ax.axvline(100, color=C_RED, ls="--", lw=1)
    # 지연 최악 5개 라벨
    for r in sorted(marked, key=lambda r: -r["delay_from_release"])[:5]:
        ax.annotate(f"B{r['id']}", (r["occ_at_release"] * 100, max(1, r["delay_from_release"])),
                    fontsize=7, color=C_DARK, xytext=(3, 3), textcoords="offset points")
    ax.set_yscale("log")
    ax.set_xlabel("bay occupancy at desired entry (release time)  [%]")
    ax.set_ylabel("forcing delay = entry - release  [days, log]")
    ax.set_xlim(-3, 105)
    n_low = sum(1 for r in marked if r["occ_at_release"] < 0.5)
    ax.set_title(f"{meta['prob']}  forced anatomy  ({n_low}/{len(marked)} forced at <50% occupancy = room existed)")
    ax.legend(loc="upper left", fontsize=8)
    ax.grid(True, which="both", alpha=0.25)
    fig.tight_layout()
    fig.savefig(out, dpi=110)
    plt.close(fig)
    return True


def plot_z1_waterfall(recs, meta, out):
    """패널4: tardiness 기여 내림차순 막대 + 누적 Z1 곡선, forced/isolated 기여분을 색으로 분리."""
    tardy = sorted([r for r in recs if r["tardiness"] > 0],
                   key=lambda r: -r["tardiness"])
    if not tardy:
        return False
    total = sum(r["tardiness"] for r in tardy)
    fig, ax = plt.subplots(figsize=(11, 5.5))
    xs = range(len(tardy))
    cols = [C_RED if r["forced_phaseB"] else (C_AMBER if r["isolated"] else C_BLUE) for r in tardy]
    ax.bar(xs, [r["tardiness"] for r in tardy], color=cols, width=1.0, zorder=2)
    ax2 = ax.twinx()
    cum = np.cumsum([r["tardiness"] for r in tardy]) / total * 100
    ax2.plot(xs, cum, color=C_DARK, lw=2, zorder=3)
    ax2.set_ylabel("cumulative Z1 (%)")
    ax2.set_ylim(0, 105)
    # forced/isolated이 차지하는 Z1 비율
    z_forced = sum(r["tardiness"] for r in tardy if r["forced_phaseB"])
    z_iso = sum(r["tardiness"] for r in tardy if r["isolated"] and not r["forced_phaseB"])
    n80 = int(np.searchsorted(cum, 80)) + 1
    ax.set_xlabel("blocks ranked by tardiness contribution")
    ax.set_ylabel("tardiness (days)")
    ax.set_title(f"{meta['prob']}  Z1 waterfall  (Z1={total:.0f}; forced={z_forced/total*100:.0f}%, "
                 f"isolated={z_iso/total*100:.0f}%; top-{n80} blocks = 80% of Z1)")
    ax.legend(handles=[
        Patch(facecolor=C_RED, label="forced (Phase B)"),
        Patch(facecolor=C_AMBER, label="isolated"),
        Patch(facecolor=C_BLUE, label="other tardy"),
    ], loc="center right", fontsize=8)
    ax.grid(True, axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(out, dpi=110)
    plt.close(fig)
    return True


def make_diagnostic_plots(prob_path, out_dir, geom_mode=None):
    os.makedirs(out_dir, exist_ok=True)
    prob, pre, recs, meta = solve_construction(prob_path, geom_mode=geom_mode)
    # 블록별 덤프(오프라인 분석용)
    with open(os.path.join(out_dir, "diag_blocks.json"), "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "blocks": recs}, f, ensure_ascii=False)
    result = {"meta": meta, "pngs": [], "errors": []}
    if not _HAVE_MPL:
        result["errors"].append("matplotlib 없음 - JSON만 기록")
        return result
    panels = [
        ("bay_timeline_gantt.png", plot_gantt),
        ("bay_occupancy_heatmap.png", plot_occupancy_heatmap),
        ("forced_anatomy_scatter.png", plot_forced_anatomy),
        ("z1_waterfall.png", plot_z1_waterfall),
    ]
    for name, fn in panels:
        p = os.path.join(out_dir, name)
        try:
            ok = fn(recs, meta, p)
            if ok is not False:
                result["pngs"].append(p)
        except Exception as e:
            try:
                plt.close("all")
            except Exception:
                pass
            result["errors"].append(f"{name}: {type(e).__name__}: {e}")
    return result


def main():
    ap = argparse.ArgumentParser(description="forced-placement diagnostic visualization (REPLAY MVP)")
    ap.add_argument("prob", help="문제 JSON 경로 (예: train/prob_27.json 또는 train/prob_27)")
    ap.add_argument("--out", default=None, help="출력 폴더 (기본 perf_out/diag/<prob>)")
    ap.add_argument("--geom-mode", default=None)
    args = ap.parse_args()

    prob_path = args.prob
    if not prob_path.endswith(".json"):
        prob_path = prob_path + ".json"
    if not os.path.isabs(prob_path):
        prob_path = str(_ROOT / prob_path)
    name = os.path.splitext(os.path.basename(prob_path))[0]
    out_dir = args.out or str(_ROOT / "perf_out" / "diag" / name)

    print(f"[viz_diag] solving {name} (construction) ...", flush=True)
    res = make_diagnostic_plots(prob_path, out_dir, geom_mode=args.geom_mode)
    m = res["meta"]
    print(f"[viz_diag] {name}: n_blocks={m['n_blocks']} bays={m['n_bays']} "
          f"feasible={m['feasible']} Z1={m['Z1']} forced={m['forced_count']} "
          f"isolated={m['isolated_count']} tardy={m['tardy_count']} "
          f"(pre {m['t_pre_s']}s, place {m['t_place_s']}s)", flush=True)
    for p in res["pngs"]:
        print("  wrote", os.path.relpath(p, _ROOT), flush=True)
    for e in res["errors"]:
        print("  ERR", e, flush=True)


if __name__ == "__main__":
    main()
