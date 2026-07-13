# scripts/ab_dispatch.py
"""dispatch vs rescue-baseline A/B. rescue §5와 같은 프로토콜: 문제마다 새
프로세스에서 algorithm(prob, T) 실행, T*1.15 초과 시 하드킬(-1 처리).
같은 머신·직렬 실행 전제 (플레이북 §8-1/2).

사용: python scripts/ab_dispatch.py           # 전체 프로브 x {30, 60}
      python scripts/ab_dispatch.py --runner prob_20 30 out.json   # 내부용"""
import json
import os
import pathlib
import statistics
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PROBE = ["prob_1", "prob_5", "prob_9", "prob_14", "prob_20",
         "prob_23", "prob_26", "prob_27", "prob_38", "prob_39"]
BUDGETS = [30.0, 60.0]

# rescue 기준값 (perf_out_rescue/perf_report_rescue.md §5; -1 = TIMEOUT)
RESCUE = {
    30.0: {"prob_1": 25674987, "prob_5": 20486012, "prob_9": 186069505,
           "prob_14": 229853759, "prob_20": -1, "prob_23": -1,
           "prob_26": -1, "prob_27": -1, "prob_38": 2159851296, "prob_39": -1},
    60.0: {"prob_1": 15594841, "prob_5": 6541655, "prob_9": 85924201,
           "prob_14": 205622345, "prob_20": 116895182, "prob_23": -1,
           "prob_26": 671509472, "prob_27": -1, "prob_38": 1905057666,
           "prob_39": -1},
}


def _runner(name, T, out_path):
    from myalgorithm import algorithm
    from Phase2.crane import _load_utils
    prob_info = json.load(open(ROOT / "train" / f"{name}.json", encoding="utf-8"))
    t0 = time.perf_counter()
    sol = algorithm(prob_info, T)
    wall = time.perf_counter() - t0
    chk = _load_utils().check_feasibility(prob_info, sol)
    json.dump({"obj": chk.get("objective") if chk["feasible"] else -1,
               "feasible": chk["feasible"], "wall": wall},
              open(out_path, "w", encoding="utf-8"))


def _run_one(name, T, dispatch):
    out = ROOT / "perf_out_dispatch_ab" / f"tmp_{name}_{int(T)}.json"
    out.parent.mkdir(exist_ok=True)
    env = dict(os.environ)
    env.pop("OGC_PORTFOLIO", None)
    if dispatch:
        env["OGC_PORTFOLIO"] = "dispatch"
    p = subprocess.Popen([sys.executable, __file__, "--runner", name, str(T), str(out)],
                         env=env, cwd=str(ROOT))
    try:
        p.wait(timeout=T * 1.15)
    except subprocess.TimeoutExpired:
        p.kill()
        return {"obj": -1, "feasible": False, "wall": T * 1.15, "timeout": True}
    try:
        return json.load(open(out, encoding="utf-8"))
    except Exception:
        return {"obj": -1, "feasible": False, "wall": None, "crash": True}


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--runner":
        _runner(sys.argv[2], float(sys.argv[3]), sys.argv[4])
        return
    rows = []
    log = open(ROOT / "perf_out_dispatch_ab" / "ab_results.jsonl", "a", encoding="utf-8")
    for T in BUDGETS:
        for name in PROBE:
            rec = {"prob": name, "T": T,
                   "rescue": RESCUE[T][name],
                   "dispatch": _run_one(name, T, dispatch=True)}
            rows.append(rec)
            log.write(json.dumps(rec) + "\n")
            log.flush()
            print(f"{name} T={T}: rescue={rec['rescue']} "
                  f"dispatch={rec['dispatch']['obj']} wall={rec['dispatch']['wall']}")
    # -- 게이트 요약 --
    for T in BUDGETS:
        sub = [r for r in rows if r["T"] == T]
        n_neg1 = sum(1 for r in sub if r["dispatch"]["obj"] == -1)
        deltas = [(r["dispatch"]["obj"] - r["rescue"]) / r["rescue"] * 100.0
                  for r in sub if r["rescue"] > 0 and r["dispatch"]["obj"] > 0]
        print(f"\nT={T}: dispatch -1 count = {n_neg1} "
              f"(G1 기준 T=30: 0), median dObj = "
              f"{statistics.median(deltas):.1f}% (G2 기준 T=60: <= 0%)")
        for pname in ("prob_26", "prob_27", "prob_38", "prob_39"):
            r = next(x for x in sub if x["prob"] == pname)
            if r["rescue"] > 0 and r["dispatch"]["obj"] > 0:
                d = (r["dispatch"]["obj"] - r["rescue"]) / r["rescue"] * 100.0
                print(f"  {pname}: {d:+.1f}% (G2 개별 기준: <= -10%)")
            else:
                print(f"  {pname}: rescue={r['rescue']} dispatch={r['dispatch']['obj']}")


if __name__ == "__main__":
    main()
