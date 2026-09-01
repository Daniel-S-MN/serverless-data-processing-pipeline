# Pandas vs. Polars Benchmark Report

## Methodology

Both libraries perform the same four-stage workload — read, parse/cast, row-level validation (structural + business-rule taxonomy), aggregation — against identical synthetic transaction datasets at three sizes. Implementations live in `src/benchmark_ops/pandas_ops.py` and `src/benchmark_ops/polars_ops.py`, each written in that library's own idioms rather than a literal translation of the other. Both were verified to produce identical row-level pass/fail counts on the same input before any timing was measured — correctness was confirmed first, independent of performance.

This benchmarks the two libraries against an equivalent workload, not the project's actual deployed Lambda code. The Lambda's real handler (`src/lambda/validation.py`) is deliberately plain Python/stdlib, since neither Pandas nor Polars packages cleanly into a Lambda zip without a Layer or container image — see the main README's "Where Polars Fits" section for that reasoning.

**Timing:** 2 warmup runs (discarded) + 10 measured runs, in-process, per library per dataset size. Both minimum and mean are reported — minimum represents best-case performance under ideal conditions, mean represents typical expected performance; a single average alone can be skewed by one noisy run (background processes, OS scheduling, etc.).

**Memory:** peak RSS (resident set size), measured differently from timing and deliberately so. `resource.getrusage().ru_maxrss` is a high-water mark for a process's entire lifetime — it never resets — so measuring it repeatedly inside one long-running process would only report "whichever run so far used the most," not independent readings. Each memory measurement instead runs in its own fresh, isolated subprocess (`scripts/memory_worker.py`), so every reading is a genuinely independent measurement. 3 isolated runs per library per size (fewer than the 10 timing runs, since each pays full process-startup cost and peak RSS is comparatively stable run-to-run).

**Dataset sizes:** 50,000 / 500,000 / 2,000,000 rows, generated via `src/generate_dataset.py --mode benchmark` with a fixed seed for reproducibility.

## Machine Specs

| | |
|---|---|
| Platform | macOS-26.6.2-arm64 |
| Processor | Apple Silicon (arm64) |
| CPU cores | 10 |
| Python | 3.14.3 |

All numbers below are specific to this machine. They are not claimed to generalize to other hardware — reported so the results are reproducible and so a reader can judge their plausibility.

## Results

### Total time (seconds)

| Dataset | Pandas (min / mean) | Polars (min / mean) | Speedup (mean) |
|---|---|---|---|
| Small (50K rows) | 0.081 / 0.082 | 0.013 / 0.014 | **6.1x** |
| Medium (500K rows) | 0.899 / 0.915 | 0.169 / 0.173 | **5.3x** |
| Large (2M rows) | 3.952 / 3.996 | 0.777 / 0.793 | **5.0x** |

### Time by stage, mean (seconds)

| Dataset | Stage | Pandas | Polars |
|---|---|---|---|
| Small | Read | 0.036 | 0.002 |
| Small | Parse | 0.011 | 0.003 |
| Small | Validate | 0.033 | 0.008 |
| Small | Aggregate | 0.002 | 0.001 |
| Medium | Read | 0.348 | 0.014 |
| Medium | Parse | 0.104 | 0.026 |
| Medium | Validate | 0.444 | 0.131 |
| Medium | Aggregate | 0.020 | 0.002 |
| Large | Read | 1.388 | 0.056 |
| Large | Parse | 0.415 | 0.103 |
| Large | Validate | 2.113 | 0.631 |
| Large | Aggregate | 0.080 | 0.004 |

### Peak memory (MB)

| Dataset | Pandas (min / mean) | Polars (min / mean) | Polars uses |
|---|---|---|---|
| Small (50K rows) | 118.48 / 118.52 | 118.41 / 119.19 | ~equal |
| Medium (500K rows) | 363.11 / 363.24 | 498.94 / 501.21 | **+38% more** |
| Large (2M rows) | 1216.28 / 1216.37 | 1642.22 / 1651.83 | **+36% more** |

## Findings

**Polars is substantially faster at every size tested — the gap does not require a large dataset to appear.** The common assumption going into this benchmark was that Polars' advantage would only become worthwhile past some size threshold, with the two performing similarly on smaller data. The measured results don't support that: Polars was already ~6x faster on the total workload at 50,000 rows, and the speedup, if anything, was *slightly smaller* at 2,000,000 rows (5.0x) than at 50,000 (6.1x). The **read** stage shows the most dramatic and consistent gap (15-25x faster across all three sizes) — likely Polars' multi-threaded CSV reader taking advantage of this machine's 10 CPU cores, versus Pandas' largely single-threaded read path.

**Polars used more peak memory, not less, at medium and large scale.** This was the more genuinely surprising result. At 500K and 2M rows, Polars' peak memory was 36-38% higher than Pandas', not lower. At 50K rows the two were essentially equal — small enough that fixed process/interpreter overhead likely dominates over actual data size. A plausible (not confirmed) explanation for the memory gap: both implementations read every column as strings (`dtype=str` in Pandas, `infer_schema_length=0` in Polars) to keep the two implementations comparable, and the `validate()` stage in both builds several additional derived columns rather than checking values inline — Polars' Arrow-backed columnar memory model may have different overhead characteristics than Pandas' NumPy/object-dtype columns for this specific string-heavy, many-derived-column workload. This wasn't investigated further; it's flagged here as an honest finding rather than an explained one.

**Practical takeaway:** for this workload shape, Pandas remains a reasonable choice when memory is the binding constraint (e.g. running inside a memory-limited container or Lambda) and processing time isn't critical. Polars is the clear choice when speed matters and memory headroom is available — which, given how early its advantage appears here, is most cases at any of the sizes actually tested. The originally expected "use whichever below X rows, switch to Polars past X" framing doesn't hold up against this data; a more accurate framing is "Polars is consistently and substantially faster across this entire size range, at the cost of meaningfully higher peak memory."

## Limitations

- Single machine, single run of the full benchmark — not repeated across multiple sessions or hardware.
- The memory-overhead explanation above is a hypothesis, not a confirmed root cause — profiling each stage's memory contribution individually would be needed to say more definitively.
- Both implementations read all columns as strings rather than allowing native type inference, to keep the comparison fair — a "let each library infer types natively" variant might show a different picture, particularly for Polars, whose native CSV type inference is a commonly cited performance advantage that this methodology deliberately doesn't exercise.
- Not tested beyond 2,000,000 rows — it's possible either the speed or memory gap changes trajectory at significantly larger scale.
