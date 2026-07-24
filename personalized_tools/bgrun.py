# personalized_tools/bgrun.py -- detached benchmark runner for OGC_2026.
#
# usage examples:
#   # 1) Run all train problems (prob_1~prob_40), 10 min each, 4 at a time.
#   venv\Scripts\python.exe personalized_tools\bgrun.py start -p 1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40 -T 600 -j 4 --cfg k3r8 --tag bg
#
#   # 2) Run only selected problems.
#   venv\Scripts\python.exe personalized_tools\bgrun.py start -p 26,28,31,30 -T 600 -j 4 --cfg k3r8 --tag mytest
#
#   # 3) Repeat the same problem by listing it multiple times.
#   venv\Scripts\python.exe personalized_tools\bgrun.py start -p 28,28,28 -T 600 -j 3 --cfg k3r8 --tag p28x3
#
#   # 4) Check status / stop. Use the exact same tag used for start.
#   venv\Scripts\python.exe personalized_tools\bgrun.py status --tag bg
#   venv\Scripts\python.exe personalized_tools\bgrun.py stop   --tag bg
#
# option notes:
#   -p       Problem number list. 26 means train/prob_26.json.
#   -T       Seconds per problem. 600 = 10 min.
#   -j       Number of parallel workers. -j 4 runs 4 problems at once.
#   --cfg    k3r8/k1r16 use CFG below. algo calls myalgorithm.algorithm.
#   --tag    Run label. Example --tag bg writes:
#              reports/bgrun/bg.jsonl       results
#              reports/bgrun/bg.log         progress log
#              reports/bgrun/bg.DONE        done marker
#              Windows task name: OGC_bgrun_bg
#
# internal commands:
#   drive / run
import argparse
import ctypes
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
sys.path.insert(0, str(ROOT))
OUT = ROOT / "reports" / "bgrun"
CREATE_NO_WINDOW = 0x08000000
ERROR_ALREADY_EXISTS = 183

CFG = {
    "k3r8": dict(xi=0.3, seed=1, rst=8, kappa=3.0, F=8),
    "k1r16": dict(xi=0.5, seed=5, rst=16, kappa=1.0, F=24),
}


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
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel32.CreateMutexW(None, False, name)
    if not handle:
        return None
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return None
    return handle


def _release_drive_mutex(handle):
    if handle:
        ctypes.windll.kernel32.CloseHandle(handle)


def cmd_run(a):
    prob = a.prob if a.prob.startswith("prob_") else f"prob_{a.prob}"
    with open(ROOT / "train" / f"{prob}.json", encoding="utf-8") as f:
        pi = json.load(f)

    t0 = time.perf_counter()
    if a.cfg in ("algo", "algo_noscarce"):
        from myalgorithm import algorithm
        from Phase2.crane import _load_utils

        old_disable = os.environ.get("OGC_DISABLE_SCARCE_ORDER")
        if a.cfg == "algo_noscarce":
            os.environ["OGC_DISABLE_SCARCE_ORDER"] = "1"
        try:
            sol = algorithm(pi, float(a.T))
        finally:
            if old_disable is None:
                os.environ.pop("OGC_DISABLE_SCARCE_ORDER", None)
            else:
                os.environ["OGC_DISABLE_SCARCE_ORDER"] = old_disable
        chk = _load_utils().check_feasibility(pi, sol) if sol is not None else {}
        rec = dict(prob=prob, cfg=a.cfg, T=float(a.T),
                   wall=round(time.perf_counter() - t0, 1),
                   ok=bool(chk.get("feasible", False)),
                   obj=chk.get("objective"),
                   Z1=chk.get("Z1"),
                   Z2=chk.get("Z2"),
                   Z3=chk.get("Z3"))
    else:
        from Phase0.preprocess import preprocess
        from Phase2.config import Phase2Config
        from Outer.config import OuterConfig
        from Outer.alns import alns

        c = CFG[a.cfg]
        pre = preprocess(pi)
        cfg = OuterConfig(
            xi=c["xi"],
            seed=c["seed"],
            restart_stall=c["rst"],
            phase2=Phase2Config(
                atc_kappa=c["kappa"],
                dispatch_admit_fail_stop=c["F"],
            ),
        )
        s, st = alns(pi, pre, float(a.T), cfg)
        rec = dict(prob=prob, cfg=a.cfg, T=float(a.T),
                   obj=(s.objective if s.feasible else None), Z1=s.Z1,
                   iters=st["iters"], restarts=st["restarts"],
                   wall=round(time.perf_counter() - t0, 1))

    pathlib.Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(rec, f)


def cmd_drive(a):
    if a.args:
        with open(ROOT / a.args, encoding="utf-8") as f:
            saved = json.load(f)
        a.probs = saved["probs"]
        a.T = saved["T"]
        a.j = saved["j"]
        a.cfg = saved["cfg"]
        a.tag = saved["tag"]

    try:
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
    j = 1 if a.cfg in ("algo", "algo_noscarce") else max(1, int(a.j))
    say(f"START tag={a.tag} cfg={a.cfg} T={a.T} j={j} probs={probs}")

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
                    stdout=subprocess.DEVNULL,
                    stderr=ef,
                    cwd=str(ROOT),
                    env=env,
                    creationflags=CREATE_NO_WINDOW,
                )
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


def cmd_start(a):
    OUT.mkdir(parents=True, exist_ok=True)
    jsonl, logp, done = _paths(a.tag)
    if done.exists():
        done.unlink()

    tn = f"OGC_bgrun_{a.tag}"
    argsp = _args_path(a.tag)
    argsp.write_text(json.dumps(dict(probs=a.p, T=a.T, j=a.j, cfg=a.cfg, tag=a.tag)),
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
    subprocess.run(["schtasks", "/Delete", "/TN", tn, "/F"], capture_output=True)
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
    j = 1 if a.cfg in ("algo", "algo_noscarce") else int(a.j)
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
                      f"Z1={r.get('Z1')} it={r.get('iters')} wall={r.get('wall')}s")
            else:
                print(f"  {r.get('prob')}: {r}")
    print(f"records={n}, done={done.exists()}")


def cmd_stop(a):
    tn = f"OGC_bgrun_{a.tag}"
    subprocess.run(["schtasks", "/End", "/TN", tn], capture_output=True)
    subprocess.run(["schtasks", "/Delete", "/TN", tn, "/F"], capture_output=True)
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
          "Where-Object { $_.CommandLine -like '*bgrun.py*' -and "
          "$_.CommandLine -notlike '* stop *' -and ("
          f"$_.CommandLine -like '*{a.tag}.args.json*' -or "
          f"$_.CommandLine -like '*_{a.tag}_*.json*') }} | "
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
    s.add_argument("--cfg", default="k3r8", choices=["k3r8", "k1r16", "algo", "algo_noscarce"])
    s.add_argument("--tag", default="bg")

    d = sub.add_parser("drive")
    d.add_argument("--args")
    d.add_argument("--probs")
    d.add_argument("-T", default="600")
    d.add_argument("-j", default="4")
    d.add_argument("--cfg", default="k3r8", choices=["k3r8", "k1r16", "algo", "algo_noscarce"])
    d.add_argument("--tag", default="bg")

    r = sub.add_parser("run")
    r.add_argument("--prob", required=True)
    r.add_argument("-T", default="600")
    r.add_argument("--cfg", default="k3r8", choices=["k3r8", "k1r16", "algo", "algo_noscarce"])
    r.add_argument("--out", required=True)

    for name in ("status", "stop"):
        x = sub.add_parser(name)
        x.add_argument("--tag", default="bg")

    a = ap.parse_args()
    {"start": cmd_start, "drive": cmd_drive, "run": cmd_run,
     "status": cmd_status, "stop": cmd_stop}[a.cmd](a)
