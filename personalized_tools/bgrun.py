# personalized_tools/bgrun.py -- detached 4-parallel benchmark runner.
#
# usage examples:
#   # 1) train 전체(prob_1~prob_40)를 문제당 10분씩, 4개 병렬로 백그라운드 실행
#   venv\Scripts\python.exe personalized_tools\bgrun.py start -p 1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40 -T 600 -j 4 --cfg k3r8 --tag bg
#
#   # 2) 일부 문제만 테스트 실행: prob_26, prob_28, prob_31, prob_30
#   venv\Scripts\python.exe personalized_tools\bgrun.py start -p 26,28,31,30,39,40,35,36,37,38,32,33 -T 600 -j 4 --cfg k3r8 --tag mytest
#
#   # 3) 같은 문제를 여러 번 돌리고 싶으면 -p에 반복해서 적기
#   venv\Scripts\python.exe personalized_tools\bgrun.py start -p 28,28,28 -T 600 -j 3 --cfg k3r8 --tag p28x3
#
#   # 4) 상태 확인 / 중단
#   venv\Scripts\python.exe personalized_tools\bgrun.py status --tag bg
#   venv\Scripts\python.exe personalized_tools\bgrun.py stop   --tag bg
#
#   # 5) cfg 선택: --cfg k3r8 또는 --cfg k1r16 또는 --cfg algo
#
# option notes:
#   -p       실행할 문제 번호 목록. 26은 train/prob_26.json을 뜻함.
#   -T       문제 하나당 실행 시간(초). 600 = 10분.
#   -j       동시에 돌릴 문제 개수. 예: -j 4면 4개씩 병렬 실행.
#   --cfg    알고리즘 설정 이름. k3r8/k1r16은 이 파일의 CFG 값 사용, algo는 myalgorithm.algorithm 사용.
#   --tag    실행 묶음 이름. 결과/로그/작업 이름을 구분하는 라벨.
#            예: --tag bg이면
#              reports/bgrun/bg.jsonl       결과
#              reports/bgrun/bg.log         로그
#              reports/bgrun/bg.DONE        완료 마커
#              Windows 작업 이름: OGC_bgrun_bg
#            그러므로 상태 확인/중단도 같은 tag를 그대로 써야 함:
#              venv\Scripts\python.exe personalized_tools\bgrun.py status --tag bg
#              venv\Scripts\python.exe personalized_tools\bgrun.py stop   --tag bg

import argparse
import datetime
import json
import os
import pathlib
import subprocess
import sys
import time

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
           "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))   # for child run process Phase0/Outer/myalgorithm imports
OUT = ROOT / "reports" / "bgrun"
CREATE_NO_WINDOW = 0x08000000   # needed for nested Popen under headless schtasks

CFG = {"k3r8": dict(xi=0.3, seed=1, rst=8, kappa=3.0, F=8),
       "k1r16": dict(xi=0.5, seed=5, rst=16, kappa=1.0, F=24)}

def _cyclex_env() -> dict:
    """Optional switch: OGC_CYCLEX=stall[,nodes[,max_cycles[,realize_k]]]."""
    raw = os.environ.get("OGC_CYCLEX", "").strip()
    if not raw:
        return {}
    cfg = {"cyclex_stall": 8, "cyclex_nodes": 24, "cyclex_max_cycles": 20000}
    if raw in {"1", "true", "TRUE", "on", "ON", "yes", "YES"}:
        return cfg
    try:
        parts = [p.strip() for p in raw.split(",")]
        if len(parts) > 0 and parts[0]:
            cfg["cyclex_stall"] = max(1, int(float(parts[0])))
        if len(parts) > 1 and parts[1]:
            cfg["cyclex_nodes"] = max(2, int(float(parts[1])))
        if len(parts) > 2 and parts[2]:
            cfg["cyclex_max_cycles"] = max(1, int(float(parts[2])))
        if len(parts) > 3 and parts[3]:
            cfg["cyclex_realize_k"] = max(1, int(float(parts[3])))

    except Exception:
        pass
    return cfg


def _inbay_env() -> dict:
    """Optional switch: OGC_INBAY=stall[,realize_k[,bays[,top]]]."""
    raw = os.environ.get("OGC_INBAY", "").strip()
    if not raw:
        return {}
    cfg = {"inbay_stall": 8, "inbay_realize_k": 4, "inbay_bays": 2, "inbay_top": 10}
    if raw in {"1", "true", "TRUE", "on", "ON", "yes", "YES"}:
        return cfg
    try:
        parts = [p.strip() for p in raw.split(",")]
        if len(parts) > 0 and parts[0]:
            cfg["inbay_stall"] = max(1, int(float(parts[0])))
        if len(parts) > 1 and parts[1]:
            cfg["inbay_realize_k"] = max(1, int(float(parts[1])))
        if len(parts) > 2 and parts[2]:
            cfg["inbay_bays"] = max(1, int(float(parts[2])))
        if len(parts) > 3 and parts[3]:
            cfg["inbay_top"] = max(2, int(float(parts[3])))
    except Exception:
        pass
    return cfg

def _paths(tag):
    return OUT / f"{tag}.jsonl", OUT / f"{tag}.log", OUT / f"{tag}.DONE"


def _args_path(tag):
    return OUT / f"{tag}.args.json"


def _cmd_path(tag):
    return OUT / f"{tag}.cmd"


def _lock_path(tag):
    return OUT / f"{tag}.lockdir"


def _remove_lock(tag):
    lockp = _lock_path(tag)
    try:
        for child in lockp.iterdir():
            child.unlink()
        lockp.rmdir()
    except FileNotFoundError:
        pass
    except NotADirectoryError:
        lockp.unlink()
    try:
        (OUT / f"{tag}.lock").unlink()
    except FileNotFoundError:
        pass


def _python_exe():
    venv_py = ROOT / "venv" / "Scripts" / "python.exe"
    return str(venv_py if venv_py.exists() else pathlib.Path(sys.executable))


def _acquire_drive_mutex(tag):
    safe_tag = "".join(ch if ch.isalnum() else "_" for ch in tag)
    name = f"Local\\OGC_bgrun_{safe_tag}"
    kernel32 = __import__("ctypes").windll.kernel32
    handle = kernel32.CreateMutexW(None, False, name)
    if not handle:
        return None
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(handle)
        return None
    return handle


def _release_drive_mutex(handle):
    if handle:
        __import__("ctypes").windll.kernel32.CloseHandle(handle)


# ---------------------------------------------------------------- single run --
def cmd_run(a):
    if getattr(a, "cyclex", ""):
        os.environ["OGC_CYCLEX"] = a.cyclex
    if getattr(a, "inbay", ""):
        os.environ["OGC_INBAY"] = a.inbay
    prob = a.prob if a.prob.startswith("prob_") else f"prob_{a.prob}"
    with open(ROOT / "train" / f"{prob}.json", encoding="utf-8") as f:
        pi = json.load(f)
    t0 = time.perf_counter()
    if a.cfg == "algo":
        from myalgorithm import algorithm
        from Phase2.crane import _load_utils
        sol = algorithm(pi, float(a.T))
        rec = dict(prob=prob, cfg="algo", T=float(a.T),
                   wall=round(time.perf_counter() - t0, 1),
                   ok=sol is not None)
        if sol is not None:
            try:
                chk = _load_utils().check_feasibility(pi, sol)
                rec.update(obj=chk.get("objective"), Z1=chk.get("obj1"),
                           Z2=chk.get("obj2"), Z3=chk.get("obj3"),
                           feasible=chk.get("feasible", False), stage=chk.get("stage"))
            except Exception as e:
                rec.update(error=repr(e))
    else:
        from Phase0.preprocess import preprocess
        from Phase2.config import Phase2Config
        from Outer.config import OuterConfig
        from Outer.alns import alns
        c = CFG[a.cfg]
        pre = preprocess(pi)
        cfg = OuterConfig(xi=c["xi"], seed=c["seed"], restart_stall=c["rst"],
                          **_cyclex_env(),
                          **_inbay_env(),
                          phase2=Phase2Config(atc_kappa=c["kappa"],
                                              dispatch_admit_fail_stop=c["F"]))
        s, st = alns(pi, pre, float(a.T), cfg)
        rec = dict(prob=prob, cfg=a.cfg, T=float(a.T),
                   obj=(s.objective if s.feasible else None), Z1=s.Z1,
                   iters=st["iters"], restarts=st["restarts"],
                   cyclex=st.get("cyclex", 0), cyclex_realized=st.get("cyclex_realized", 0),
                   cyclex_improved=st.get("cyclex_improved", 0),
                   cyclex_proxy_hit=st.get("cyclex_proxy_hit", 0),
                   cyclex_proxy_miss=st.get("cyclex_proxy_miss", 0),
                   cyclex_proxy_best=st.get("cyclex_proxy_best"),
                   cyclex_real_best=st.get("cyclex_real_best"),
                   cyclex_nodes_sum=st.get("cyclex_nodes_sum", 0),
                   cyclex_checked_sum=st.get("cyclex_checked_sum", 0),
                   cyclex_disabled=st.get("cyclex_disabled", 0),
                   cyclex_time_s=round(st.get("cyclex_time_s", 0.0), 1),
                   inbay=st.get("inbay", 0), inbay_realized=st.get("inbay_realized", 0),
                   inbay_improved=st.get("inbay_improved", 0),
                   inbay_real_best=st.get("inbay_real_best"),
                   inbay_time_s=round(st.get("inbay_time_s", 0.0), 1),
                   wall=round(time.perf_counter() - t0, 1))
    pathlib.Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(rec, f)


# --------------------------------------------------------------- driver --
def cmd_drive(a):
    import ctypes
    if a.args:
        with open(ROOT / a.args, encoding="utf-8") as f:
            saved = json.load(f)
        a.probs = saved["probs"]
        a.T = saved["T"]
        a.j = saved["j"]
        a.cfg = saved["cfg"]
        a.tag = saved["tag"]
        if saved.get("cyclex"):
            os.environ["OGC_CYCLEX"] = saved["cyclex"]
        else:
            os.environ.pop("OGC_CYCLEX", None)
        if saved.get("inbay"):
            os.environ["OGC_INBAY"] = saved["inbay"]
        else:
            os.environ.pop("OGC_INBAY", None)
    elif getattr(a, "cyclex", ""):
        os.environ["OGC_CYCLEX"] = a.cyclex
    try:   # inhibit idle/display sleep while running
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
    except Exception:
        pass
    jsonl, logp, done = _paths(a.tag)
    OUT.mkdir(parents=True, exist_ok=True)
    mutex = _acquire_drive_mutex(a.tag)
    if mutex is None:
        with open(logp, "a", encoding="utf-8") as log:
            log.write(f"[{time.strftime('%H:%M:%S')}] SKIP duplicate drive tag={a.tag}\n")
        return
    log = open(logp, "a", encoding="utf-8")

    def say(msg):
        log.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        log.flush()


    probs = [p.strip() for p in a.probs.split(",") if p.strip()]
    j = 1 if a.cfg == "algo" else max(1, int(a.j))
    say(f"START tag={a.tag} cfg={a.cfg} T={a.T} j={j} probs={probs} cyclex={os.environ.get('OGC_CYCLEX', '') or 'off'} inbay={os.environ.get('OGC_INBAY', '') or 'off'}")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    rec_out = open(jsonl, "a", encoding="utf-8")
    t0 = time.time()
    try:
        for w0 in range(0, len(probs), j):
            wave = probs[w0:w0 + j]
            procs = []
            for k, p in enumerate(wave):
                oj = OUT / f"_{a.tag}_{w0 + k}.json"
                ej = oj.with_suffix(".err")
                ef = open(ej, "w", encoding="utf-8")
                pr = subprocess.Popen(
                    [_python_exe(), str(ROOT / "personalized_tools" / "bgrun.py"), "run",
                     "--prob", p, "-T", str(a.T), "--cfg", a.cfg, "--out", str(oj)],
                    stdout=subprocess.DEVNULL, stderr=ef,
                    cwd=str(ROOT), env=env, creationflags=CREATE_NO_WINDOW)
                procs.append((p, pr, oj, ej, ef))
            for p, pr, oj, ej, ef in procs:
                pr.wait()
                ef.close()
            for p, pr, oj, ej, ef in procs:
                try:
                    with open(oj, encoding="utf-8") as f:
                        rec_out.write(json.dumps(json.load(f)) + "\n")
                    oj.unlink()
                    ej.unlink()
                except Exception:
                    tail = ""
                    try:
                        tail = ej.read_text(encoding="utf-8", errors="replace")[-400:]
                    except Exception:
                        pass
                    rec_out.write(json.dumps(dict(prob=p, error=tail or "no output")) + "\n")
                rec_out.flush()
            say(f"wave {w0 // j + 1}/{(len(probs) + j - 1) // j} done "
                f"({', '.join(wave)}) elapsed={time.time() - t0:.0f}s")
        done.write_text("done", encoding="utf-8")
        say("ALL DONE")
    finally:
        rec_out.close()
        log.close()
        _release_drive_mutex(mutex)
        try:
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        except Exception:
            pass


# --------------------------------------------------------- start/status/stop --
def cmd_start(a):
    OUT.mkdir(parents=True, exist_ok=True)
    jsonl, logp, done = _paths(a.tag)
    if done.exists():
        done.unlink()
    tn = f"OGC_bgrun_{a.tag}"
    argsp = _args_path(a.tag)
    cyclex_value = getattr(a, "cyclex", "") or os.environ.get("OGC_CYCLEX", "")
    inbay_value = getattr(a, "inbay", "") or os.environ.get("OGC_INBAY", "")
    argsp.write_text(json.dumps(dict(probs=a.p, T=a.T, j=a.j, cfg=a.cfg, tag=a.tag,
                                    cyclex=cyclex_value, inbay=inbay_value)),
                     encoding="utf-8")
    rel_args = argsp.relative_to(ROOT)
    cmdp = _cmd_path(a.tag)
    rel_task_log = OUT.relative_to(ROOT) / f"{a.tag}.task.log"
    cmdp.write_text(
        "@echo off\n"
        "cd /d \"%~dp0..\\..\"\n"
        f"\"venv\\Scripts\\python.exe\" \"personalized_tools\\bgrun.py\" drive --args \"{rel_args}\" "
        f">> \"{rel_task_log}\" 2>&1\n",
        encoding="utf-8",
    )
    tr = f'cmd /c ""{cmdp}""'
    subprocess.run(["schtasks", "/End", "/TN", tn], capture_output=True)
    subprocess.run(["schtasks", "/Delete", "/TN", tn, "/F"],
                   capture_output=True)
    _remove_lock(a.tag)
    start_at = (datetime.datetime.now() + datetime.timedelta(minutes=1)).strftime("%H:%M")
    r = subprocess.run(["schtasks", "/Create", "/TN", tn, "/TR", tr,
                        "/SC", "ONCE", "/ST", start_at, "/F"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print("schtasks registration failed:", r.stderr.strip())
        sys.exit(1)
    ps = (
        "$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries "
        "-DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew "
        "-ExecutionTimeLimit (New-TimeSpan -Hours 72); "
        f"Set-ScheduledTask -TaskName '{tn}' -Settings $s | Out-Null"
    )
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-Command", ps], capture_output=True)
    n = len([x for x in a.p.split(",") if x.strip()])
    j = 1 if a.cfg == "algo" else int(a.j)
    est = (n + j - 1) // j * (float(a.T) + 20)
    print(f"scheduled detached: task={tn}, start_at={start_at}")
    print(f"  problems={n}, cfg={a.cfg}, T={a.T}s, parallel={j} -> estimated ~{est / 60:.0f} min")
    print(f"  results: {jsonl}\n  log: {logp}\n  done marker: {done}")
    print(f"  status: venv\\Scripts\\python.exe personalized_tools\\bgrun.py status --tag {a.tag}")
    print("It keeps running after VS Code/terminal closes. Avoid system sleep/shutdown.")


def cmd_status(a):
    jsonl, logp, done = _paths(a.tag)
    if logp.exists():
        print("--- log tail ---")
        print("\n".join(logp.read_text(encoding="utf-8").splitlines()[-5:]))
    n = 0
    if jsonl.exists():
        print("--- results ---")
        for ln in open(jsonl, encoding="utf-8"):
            r = json.loads(ln)
            n += 1
            if "obj" in r:
                print(f"  {r['prob']:>8} {r.get('cfg','')}: obj={r['obj']:,.0f} "
                      f"Z1={r.get('Z1')} it={r.get('iters')} "
                      f"cx={r.get('cyclex', 0)}/{r.get('cyclex_improved', 0)} "
                      f"px={r.get('cyclex_proxy_hit', 0)}/{r.get('cyclex_proxy_miss', 0)} "
                      f"cxt={r.get('cyclex_time_s', 0)}s "
                      f"ib={r.get('inbay', 0)}/{r.get('inbay_improved', 0)} "
                      f"ibt={r.get('inbay_time_s', 0)}s "
                      f"wall={r.get('wall')}s")
            else:
                print(f"  {r.get('prob')}: {r}")
    print(f"records={n}, done={done.exists()}")


def cmd_stop(a):
    tn = f"OGC_bgrun_{a.tag}"
    subprocess.run(["schtasks", "/End", "/TN", tn], capture_output=True)
    subprocess.run(["schtasks", "/Delete", "/TN", tn, "/F"], capture_output=True)
    # Kill only child run/drive python processes matching this script.
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
          "Where-Object { $_.CommandLine -like '*bgrun.py*' -and "
          "$_.CommandLine -notlike '* stop *' } | "
          "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }")
    subprocess.run(["powershell", "-Command", ps], capture_output=True)
    _remove_lock(a.tag)
    print(f"stopped and unregistered: {tn}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start")
    s.add_argument("-p", required=True, help="problem list: 26,28,31,30 (repeats allowed)")
    s.add_argument("-T", default="600", help="seconds per problem, default 600=10 min")
    s.add_argument("-j", default="4", help="parallel jobs, default 4")
    s.add_argument("--cfg", default="k3r8", choices=["k3r8", "k1r16", "algo"])
    s.add_argument("--tag", default="bg")
    s.add_argument("--cyclex", default="", help="enable cyclex: stall[,nodes[,max_cycles[,realize_k]]]")
    s.add_argument("--inbay", default="", help="enable inbay: stall[,realize_k[,bays[,top]]]")
    d = sub.add_parser("drive")
    d.add_argument("--args")
    d.add_argument("--probs")
    d.add_argument("-T", default="600")
    d.add_argument("-j", default="4")
    d.add_argument("--cfg", default="k3r8", choices=["k3r8", "k1r16", "algo"])
    d.add_argument("--tag", default="bg")
    d.add_argument("--cyclex", default="")
    d.add_argument("--inbay", default="")
    r = sub.add_parser("run")
    r.add_argument("--prob", required=True)
    r.add_argument("-T", default="600")
    r.add_argument("--cfg", default="k3r8", choices=["k3r8", "k1r16", "algo"])
    r.add_argument("--out", required=True)
    r.add_argument("--cyclex", default="")
    r.add_argument("--inbay", default="")
    for name in ("status", "stop"):
        x = sub.add_parser(name)
        x.add_argument("--tag", default="bg")
    a = ap.parse_args()
    {"start": cmd_start, "drive": cmd_drive, "run": cmd_run,
     "status": cmd_status, "stop": cmd_stop}[a.cmd](a)

