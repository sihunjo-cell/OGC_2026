# viz/serve.py -- bay_viewer.html 로컬 서버 + 솔버 실행 백엔드 (표준 라이브러리만 사용)
#
# 사용법:
#   python viz\serve.py            # http://127.0.0.1:8765 서버 시작 + 브라우저 오픈
#   python viz\serve.py -p 9000    # 포트 변경
#
# 웹 UI에서 문제(train/*.json)와 timelimit을 고르고 실행하면 이 저장소의
# myalgorithm.algorithm()을 별도 프로세스로 돌린 뒤 결과를 뷰어에 자동 로드한다.
# 결과는 viz/runs/에 {"prob_info":…, "solution":…} 통합 JSON으로 저장된다
# (나중에 서버 없이 뷰어에 드래그해도 열림).

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VIZ_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(VIZ_DIR)
RUNS_DIR = os.path.join(VIZ_DIR, "runs")
TRAIN_DIR = os.path.join(ROOT, "train")
HTML_PATH = os.path.join(VIZ_DIR, "bay_viewer.html")

_lock = threading.Lock()
_run = {
    "state": "idle",       # idle | running | done | error
    "prob": None,           # ex) "prob_26.json"
    "timelimit": None,
    "started": None,        # time.time()
    "elapsed": 0.0,
    "error": None,
    "result_path": None,    # 완료된 통합 JSON 경로
    "proc": None,
    "stderr_path": None,
}


# ── 워커 모드: 솔버를 이 파일 자신을 서브프로세스로 띄워 실행 ──────────────
def worker_main(prob_path: str, timelimit: float, out_path: str) -> int:
    sys.path.insert(0, ROOT)
    os.chdir(ROOT)
    with open(prob_path, encoding="utf-8") as f:
        prob = json.load(f)
    from myalgorithm import algorithm
    t0 = time.perf_counter()
    sol = algorithm(prob, timelimit)
    wall = time.perf_counter() - t0
    if not sol or "operations" not in sol:
        print("solver returned no solution", file=sys.stderr)
        return 3
    combined = {
        "prob_info": prob,
        "solution": sol,
        "meta": {
            "prob_file": os.path.basename(prob_path),
            "timelimit": timelimit,
            "wall_s": round(wall, 1),
            "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        },
    }
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(combined, f)
    os.replace(tmp, out_path)
    return 0


# ── 서버 쪽 상태 관리 ──────────────────────────────────────────────────────
def list_probs():
    files = glob.glob(os.path.join(TRAIN_DIR, "*.json"))

    def key(p):
        m = re.search(r"(\d+)", os.path.basename(p))
        return (int(m.group(1)) if m else 1 << 30, os.path.basename(p))

    return [os.path.basename(p) for p in sorted(files, key=key)]


def poll_proc_locked():
    """_lock 보유 상태에서 호출: 러닝 중이면 프로세스 종료 여부 반영."""
    if _run["state"] != "running":
        return
    _run["elapsed"] = time.time() - _run["started"]
    proc = _run["proc"]
    rc = proc.poll()
    if rc is None:
        return
    _run["proc"] = None
    if rc == 0 and _run["result_path"] and os.path.exists(_run["result_path"]):
        _run["state"] = "done"
    else:
        tail = ""
        try:
            with open(_run["stderr_path"], encoding="utf-8", errors="replace") as f:
                tail = "".join(f.readlines()[-15:]).strip()
        except OSError:
            pass
        _run["state"] = "error"
        _run["error"] = f"solver exit code {rc}" + (f"\n{tail}" if tail else "")


def start_run(prob_file: str, timelimit: float):
    with _lock:
        poll_proc_locked()
        if _run["state"] == "running":
            return False, "이미 실행 중입니다"
        prob_path = os.path.join(TRAIN_DIR, os.path.basename(prob_file))
        if not os.path.exists(prob_path):
            return False, f"문제 파일 없음: {prob_file}"
        os.makedirs(RUNS_DIR, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        name = os.path.splitext(os.path.basename(prob_file))[0]
        out_path = os.path.join(RUNS_DIR, f"{name}_T{int(timelimit)}_{stamp}.json")
        stderr_path = os.path.join(RUNS_DIR, f"{name}_T{int(timelimit)}_{stamp}.stderr.txt")
        argv = [sys.executable, os.path.abspath(__file__), "--worker",
                prob_path, str(timelimit), out_path]
        proc = subprocess.Popen(
            argv, cwd=ROOT,
            stdout=subprocess.DEVNULL, stderr=open(stderr_path, "w", encoding="utf-8"),
        )
        _run.update(state="running", prob=os.path.basename(prob_file),
                    timelimit=timelimit, started=time.time(), elapsed=0.0,
                    error=None, result_path=out_path, proc=proc,
                    stderr_path=stderr_path)
        return True, None


def cancel_run():
    with _lock:
        if _run["state"] != "running" or _run["proc"] is None:
            return False
        _run["proc"].kill()
        _run["proc"] = None
        _run["state"] = "idle"
        _run["error"] = None
        return True


# ── HTTP 핸들러 ────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # 콘솔 소음 억제 (에러만)
        if args and str(args[1]).startswith(("4", "5")):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            with open(HTML_PATH, "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
        elif path == "/api/probs":
            self._send(200, {"probs": list_probs()})
        elif path == "/api/status":
            with _lock:
                poll_proc_locked()
                self._send(200, {k: _run[k] for k in
                                 ("state", "prob", "timelimit", "elapsed", "error")})
        elif path == "/api/result":
            with _lock:
                poll_proc_locked()
                ok = _run["state"] == "done" and _run["result_path"]
                rp = _run["result_path"]
            if ok and os.path.exists(rp):
                with open(rp, "rb") as f:
                    self._send(200, f.read())
            else:
                self._send(404, {"error": "결과 없음"})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            self._send(400, {"error": "bad json"})
            return
        if self.path == "/api/run":
            timelimit = float(body.get("timelimit") or 600)
            timelimit = max(10.0, min(timelimit, 3600.0))
            ok, err = start_run(str(body.get("prob") or ""), timelimit)
            self._send(200 if ok else 409, {"ok": ok, "error": err})
        elif self.path == "/api/cancel":
            self._send(200, {"ok": cancel_run()})
        else:
            self._send(404, {"error": "not found"})


def main():
    ap = argparse.ArgumentParser(description="OGC bay viewer server")
    ap.add_argument("-p", "--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--worker", nargs=3, metavar=("PROB", "TIMELIMIT", "OUT"),
                    help=argparse.SUPPRESS)
    args = ap.parse_args()

    if args.worker:
        sys.exit(worker_main(args.worker[0], float(args.worker[1]), args.worker[2]))

    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"* OGC bay viewer: {url}  (Ctrl+C로 종료)")
    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n종료")


if __name__ == "__main__":
    main()
