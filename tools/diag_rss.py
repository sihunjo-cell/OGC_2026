"""Run myalgorithm on an instance; report return-wall vs timelimit and peak RSS tree."""
import json
import os
import sys
import time
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def peak_rss_tree():
    try:
        import psutil
        me = psutil.Process(os.getpid())
        rss = me.memory_info().rss
        for c in me.children(recursive=True):
            try:
                rss += c.memory_info().rss
            except Exception:
                pass
        return rss
    except Exception:
        return -1


def main(path, T):
    from myalgorithm import algorithm
    prob = json.load(open(path, encoding="utf-8"))
    t0 = time.perf_counter()
    sol = algorithm(prob, float(T))
    dt = time.perf_counter() - t0
    print(f"returned in {dt:.2f}s / limit {T}s  ok={dt <= float(T)}  "
          f"sol={'None' if sol is None else 'dict'}  peakRSS_MB={peak_rss_tree() / 1e6:.0f}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
