"""
Runs ONE full workload (read -> parse -> validate -> aggregate) for
ONE library, in its own fresh process, then prints its peak RSS
memory as JSON to stdout.

This exists specifically because peak-RSS measurement (via the
resource module) is a high-water mark for the whole process's
lifetime - it never resets. Measuring memory 10 times in a shared,
long-running process would just report "whichever run so far used
the most," not 10 independent numbers. Running each measured run in
its own subprocess is what makes each memory reading genuinely
independent. Timing doesn't have this problem, so it's measured
in-process by the orchestrator instead - only memory needs this
subprocess isolation.

Usage: python3 memory_worker.py <pandas|polars> <csv_path>
"""

import json
import resource
import sys


def main():
    library = sys.argv[1]
    csv_path = sys.argv[2]

    if library == "pandas":
        from benchmark_ops.pandas_ops import run_full_workload
    elif library == "polars":
        from benchmark_ops.polars_ops import run_full_workload
    else:
        raise ValueError(f"Unknown library: {library}")

    run_full_workload(csv_path)

    # ru_maxrss units differ by platform: KB on Linux, bytes on
    # macOS. Normalize to MB either way so the reported number means
    # the same thing regardless of what machine this runs on.
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak_mb = raw / 1024 / 1024 if sys.platform == "darwin" else raw / 1024

    print(json.dumps({"peak_memory_mb": round(peak_mb, 2)}))


if __name__ == "__main__":
    main()
