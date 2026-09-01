"""
Pandas vs Polars benchmark orchestrator.

Methodology:
  - Three dataset sizes (small/medium/large), generated once via the
    existing generate_dataset.py CLI (--mode benchmark), reused across
    both libraries so the comparison is apples-to-apples.
  - TIMING: 2 warmup runs (discarded, in-process) + 10 measured runs
    (in-process) per library per size, per stage (read/parse/validate/
    aggregate) plus total. Warmups exist because a first run can be
    slower for reasons unrelated to the library itself (disk cache,
    one-time import costs). Reports min AND mean per stage - min as
    "what the library is capable of," mean as "what you'd typically
    see," since a plain average alone can be skewed by one noisy run.
  - MEMORY: measured differently, and deliberately NOT with the same
    10-run in-process loop. Peak RSS (resource.getrusage) is a
    high-water mark for a process's whole lifetime - it never resets,
    so measuring it repeatedly inside one long-running process would
    just report "whichever run so far used the most," not independent
    readings. Each memory measurement instead runs in its own fresh
    subprocess (memory_worker.py) so every reading is genuinely
    independent. Fewer repetitions (3, not 10) since each one pays
    full process-startup cost and peak RSS is a comparatively stable
    metric run-to-run, unlike wall-clock time.

Usage: python3 scripts/benchmark.py
"""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
DATA_DIR = REPO_ROOT / "data" / "sample"
RESULTS_PATH = REPO_ROOT / "data" / "benchmark_results.json"

sys.path.insert(0, str(SRC_DIR))

DATASET_SIZES = {"small": 50_000, "medium": 500_000, "large": 2_000_000}
WARMUP_RUNS = 2
MEASURED_RUNS = 10
MEMORY_RUNS = 3


def ensure_dataset(size_label: str, rows: int) -> Path:
    target = DATA_DIR / f"benchmark_{size_label}.csv"
    if target.exists():
        print(f"  Using existing {target.name}")
        return target

    print(f"  Generating {rows:,} rows for '{size_label}'...")
    subprocess.run(
        [
            sys.executable, str(SRC_DIR / "generate_dataset.py"),
            "--mode", "benchmark", "--rows", str(rows), "--seed", "42",
            "--batch-id", f"BENCH-{size_label.upper()}",
        ],
        check=True,
        cwd=SRC_DIR,
    )
    generated = DATA_DIR / "benchmark_transactions.csv"
    shutil.move(str(generated), str(target))
    return target


def time_workload(library: str, csv_path: Path) -> dict:
    if library == "pandas":
        from benchmark_ops import pandas_ops as ops
    else:
        from benchmark_ops import polars_ops as ops

    stage_times = {"read": [], "parse": [], "validate": [], "aggregate": [], "total": []}

    for i in range(WARMUP_RUNS + MEASURED_RUNS):
        t0 = time.perf_counter()
        df = ops.read(str(csv_path))
        t1 = time.perf_counter()
        df = ops.parse(df)
        t2 = time.perf_counter()
        df = ops.validate(df)
        t3 = time.perf_counter()
        ops.aggregate(df)
        t4 = time.perf_counter()

        if i < WARMUP_RUNS:
            continue  # discard warmup runs

        stage_times["read"].append(t1 - t0)
        stage_times["parse"].append(t2 - t1)
        stage_times["validate"].append(t3 - t2)
        stage_times["aggregate"].append(t4 - t3)
        stage_times["total"].append(t4 - t0)

    return {
        stage: {"min_sec": round(min(times), 4), "mean_sec": round(sum(times) / len(times), 4)}
        for stage, times in stage_times.items()
    }


def measure_memory(library: str, csv_path: Path) -> dict:
    import os
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC_DIR)

    readings = []
    for _ in range(MEMORY_RUNS):
        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "memory_worker.py"), library, str(csv_path)],
            check=True,
            cwd=SRC_DIR,
            capture_output=True,
            text=True,
            env=env,
        )
        readings.append(json.loads(result.stdout)["peak_memory_mb"])
    return {"min_mb": min(readings), "mean_mb": round(sum(readings) / len(readings), 2)}


def machine_info() -> dict:
    import os
    return {
        "platform": platform.platform(),
        "processor": platform.processor() or platform.machine(),
        "python_version": platform.python_version(),
        "cpu_count": os.cpu_count(),
    }


def main():
    print(f"Machine: {machine_info()}\n")
    results = {"machine": machine_info(), "runs": {}}

    for size_label, rows in DATASET_SIZES.items():
        print(f"=== {size_label} ({rows:,} rows) ===")
        csv_path = ensure_dataset(size_label, rows)
        results["runs"][size_label] = {"rows": rows}

        for library in ("pandas", "polars"):
            print(f"  Timing {library}...")
            timing = time_workload(library, csv_path)
            print(f"  Measuring {library} memory ({MEMORY_RUNS} isolated runs)...")
            memory = measure_memory(library, csv_path)
            results["runs"][size_label][library] = {"timing": timing, "memory": memory}
            print(f"    total: min={timing['total']['min_sec']}s mean={timing['total']['mean_sec']}s "
                  f"| memory: min={memory['min_mb']}MB mean={memory['mean_mb']}MB")
        print()

    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(results, indent=2))
    print(f"Results written to {RESULTS_PATH}")


if __name__ == "__main__":
    main()
