"""Run perf_report.py sequentially over (scoring_profile, alns_deadline_s) pairs."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PERF_REPORT = ROOT / "perf_report.py"

DEFAULT_PAIRS = [  # FIXME: tune the default sweep pair list after broader experiments.
    "baseline:55",
    "forced_risk_20:55",
]
DEFAULT_MAX_HOURS = 8.0  # FIXME: tune the default max-hours budget after broader experiments.

CSV_COLUMNS = (
    "timestamp",
    "pair",
    "scoring_profile",
    "alns_deadline_s",
    "returncode",
    "duration_sec",
    "log_path",
    "summary_path",
)


def _iso_now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _timestamp_now() -> str:
    return dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in value)


def _print(message: str) -> None:
    print(f"[{_iso_now()}] {message}", flush=True)


def _append_csv_row(path: Path, row: dict) -> None:
    write_header = not path.exists()
    with path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        if write_header:
            writer.writeheader()
        writer.writerow(row)
        f.flush()
        os.fsync(f.fileno())


def _write_readme(path: Path, start_time: dt.datetime, end_time: dt.datetime,
                  attempted: list[str], successes: int, failures: int, csv_path: Path) -> None:
    lines = [
        "# Experiment Sweep",
        "",
        f"- start time: `{start_time.isoformat(timespec='seconds')}`",
        f"- end time: `{end_time.isoformat(timespec='seconds')}`",
        f"- total duration: `{end_time - start_time}`",
        f"- pairs attempted: `{', '.join(attempted) if attempted else '-'}`",
        f"- successful runs: `{successes}`",
        f"- failed runs: `{failures}`",
        f"- sweep_runs.csv: `{csv_path}`",
        "- detailed metrics are in each raw log and each per-run perf_report output directory",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _parse_pair(value: str) -> tuple[str, float]:
    if ":" not in value:
        raise argparse.ArgumentTypeError(f"Invalid pair '{value}'. Expected profile:deadline.")
    profile, deadline_text = value.split(":", 1)
    profile = profile.strip()
    if not profile:
        raise argparse.ArgumentTypeError(f"Invalid pair '{value}'. Missing profile.")
    try:
        deadline_s = float(deadline_text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid pair '{value}'. Bad deadline.") from exc
    if deadline_s <= 0.0:
        raise argparse.ArgumentTypeError(f"Invalid pair '{value}'. Deadline must be positive.")
    return profile, deadline_s


def _build_command(python_exe: str, profile: str, deadline_s: float, run_out_dir: Path) -> list[str]:
    return [
        python_exe,
        "-u",
        str(PERF_REPORT),
        "--scoring-profile",
        profile,
        "--alns-deadline-s",
        f"{deadline_s:g}",
        "--out",
        str(run_out_dir),
    ]


def _stream_process(cmd: list[str], log_path: Path) -> int:
    with log_path.open("w", encoding="utf-8") as logf:
        logf.write(f"[{_iso_now()}] CMD {' '.join(cmd)}\n")
        logf.flush()
        proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        if proc.stdout is None:
            return proc.wait()
        for line in proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            logf.write(line)
            logf.flush()
        return proc.wait()


def _run_name(profile: str, deadline_s: float, repeat: int) -> str:
    return f"{_timestamp_now()}_{_safe_name(profile)}_d{_safe_name(f'{deadline_s:g}')}_r{repeat}"


def _should_stop(elapsed_sec: float, durations: list[float], max_seconds: float) -> bool:
    if not durations:
        return False
    avg_duration = sum(durations) / len(durations)
    return elapsed_sec + avg_duration > max_seconds


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pairs", nargs="+", type=_parse_pair, default=[_parse_pair(v) for v in DEFAULT_PAIRS])
    ap.add_argument("--max-hours", type=float, default=DEFAULT_MAX_HOURS)
    ap.add_argument("--out-dir", default=str(ROOT / "reports" / "scoring_sweep"))
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--dry-run", action="store_true")
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    out_dir = Path(args.out_dir).resolve()
    logs_dir = out_dir / "logs"
    summaries_dir = out_dir / "summaries"
    runs_dir = out_dir / "runs"
    csv_path = out_dir / "sweep_runs.csv"

    if not args.dry_run:
        logs_dir.mkdir(parents=True, exist_ok=True)
        summaries_dir.mkdir(parents=True, exist_ok=True)
        runs_dir.mkdir(parents=True, exist_ok=True)

    start_dt = dt.datetime.now()
    start_perf = time.perf_counter()
    max_seconds = args.max_hours * 3600.0
    durations: list[float] = []
    attempted: list[str] = []
    successes = 0
    failures = 0
    total_runs = len(args.pairs) * max(1, args.repeat)
    run_index = 0

    _print(
        f"sweep start pairs={[(profile, deadline) for profile, deadline in args.pairs]} "
        f"repeat={args.repeat} max_hours={args.max_hours:g} out_dir={out_dir}"
    )

    for repeat_idx in range(1, args.repeat + 1):
        for profile, deadline_s in args.pairs:
            elapsed = time.perf_counter() - start_perf
            if _should_stop(elapsed, durations, max_seconds):
                avg_duration = sum(durations) / len(durations)
                _print(
                    f"stopping before next run: elapsed={elapsed:.1f}s "
                    f"avg_run={avg_duration:.1f}s budget={max_seconds:.1f}s"
                )
                if not args.dry_run:
                    _write_readme(out_dir / "README.md", start_dt, dt.datetime.now(),
                                  attempted, successes, failures, csv_path)
                return

            run_index += 1
            pair_label = f"{profile}:{deadline_s:g}"
            attempted.append(pair_label)
            run_name = _run_name(profile, deadline_s, repeat_idx)
            log_path = logs_dir / f"{run_name}.log"
            run_out_dir = runs_dir / run_name
            summary_path = run_out_dir / "perf_report.md"
            cmd = _build_command(args.python, profile, deadline_s, run_out_dir)

            _print(
                f"run {run_index}/{total_runs} pair={pair_label} repeat={repeat_idx} "
                f"elapsed={elapsed / 3600.0:.2f}h"
            )
            _print(f"command: {' '.join(cmd)}")

            if args.dry_run:
                continue

            run_out_dir.mkdir(parents=True, exist_ok=True)
            t0 = time.perf_counter()
            returncode = _stream_process(cmd, log_path)
            duration_sec = time.perf_counter() - t0
            durations.append(duration_sec)

            if summary_path.exists():
                saved_summary_path = summary_path
            else:
                saved_summary_path = summaries_dir / f"{run_name}.txt"
                shutil.copyfile(log_path, saved_summary_path)

            _append_csv_row(csv_path, {
                "timestamp": run_name.split("_r", 1)[0],
                "pair": pair_label,
                "scoring_profile": profile,
                "alns_deadline_s": f"{deadline_s:g}",
                "returncode": returncode,
                "duration_sec": f"{duration_sec:.3f}",
                "log_path": str(log_path),
                "summary_path": str(saved_summary_path),
            })

            if returncode == 0:
                successes += 1
                _print(
                    f"completed pair={pair_label} repeat={repeat_idx} "
                    f"duration={duration_sec / 60.0:.1f}m summary={saved_summary_path}"
                )
            else:
                failures += 1
                _print(
                    f"failed pair={pair_label} repeat={repeat_idx} returncode={returncode} "
                    f"duration={duration_sec / 60.0:.1f}m log={log_path}"
                )

    if not args.dry_run:
        _write_readme(out_dir / "README.md", start_dt, dt.datetime.now(),
                      attempted, successes, failures, csv_path)
    _print(f"sweep finished successes={successes} failures={failures} duration={dt.datetime.now() - start_dt}")


if __name__ == "__main__":
    main()
