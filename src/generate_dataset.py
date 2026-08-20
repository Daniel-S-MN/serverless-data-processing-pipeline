"""
CLI for generating transaction datasets.

Two modes, sharing the same clean-record generator and schema:

  --mode seeded      Small dataset with intentionally injected, tracked
                     defects. This is the "daily file" stand-in that
                     flows through the actual pipeline in tests/demos.
                     Produces two files: the CSV and a JSON defect
                     manifest (transaction_id -> defect_name/severity).

  --mode benchmark   Large, essentially clean dataset. Exists only to
                     benchmark Pandas vs Polars — realism of individual
                     rows matters far less than volume and schema
                     consistency here.

Usage:
    python generate_dataset.py --mode seeded --rows 2000 --defect-rate 0.05 --seed 42
    python generate_dataset.py --mode benchmark --rows 1000000 --seed 42
"""

from __future__ import annotations

import argparse
import csv
import json
import random
from datetime import date
from pathlib import Path

from clean_record import generate_clean_record
from defects import DEFECT_REGISTRY
from schema import FIELDNAMES

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "sample"


def _resolve_refund_links(rows: list[dict], rng: random.Random) -> None:
    """
    Second pass: any row with transaction_type == 'refund' that
    doesn't already carry a related_transaction_id (defect rows may
    set their own, e.g. orphaned_refund) gets pointed at a real,
    already-generated non-refund transaction_id.
    """
    non_refund_ids = [
        r["transaction_id"] for r in rows if r["transaction_type"] != "refund"
    ]
    if not non_refund_ids:
        return
    for row in rows:
        if row["transaction_type"] == "refund" and not row["related_transaction_id"]:
            row["related_transaction_id"] = rng.choice(non_refund_ids)


def _generate_clean_batch(
    num_rows: int, batch_id: str, batch_day: date, rng: random.Random
) -> list[dict]:
    rows = [
        generate_clean_record(i, batch_id, batch_day, rng).to_dict()
        for i in range(1, num_rows + 1)
    ]
    _resolve_refund_links(rows, rng)
    return rows


def _write_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def generate_seeded_dataset(
    num_rows: int, defect_rate: float, seed: int, batch_id: str
) -> tuple[list[dict], dict]:
    rng = random.Random(seed)
    batch_day = date.today()
    rows = _generate_clean_batch(num_rows, batch_id, batch_day, rng)

    num_defects = max(1, int(num_rows * defect_rate))
    defect_indices = rng.sample(range(len(rows)), k=min(num_defects, len(rows)))

    manifest: dict[str, dict] = {}
    duplicate_rows_to_add = []

    for idx in defect_indices:
        defect = rng.choice(DEFECT_REGISTRY)
        original_row = rows[idx]
        mutated = defect.apply(original_row, rng)

        if mutated.pop("_defect_duplicate", False):
            # Duplicate defect: keep the original row AND queue a
            # second copy (same transaction_id) to be appended later,
            # rather than overwriting the row in place.
            duplicate_rows_to_add.append(dict(original_row))
            manifest[original_row["transaction_id"]] = {
                "defect": defect.name,
                "severity": defect.severity,
            }
            continue

        rows[idx] = mutated
        manifest[mutated.get("transaction_id", f"row_{idx}")] = {
            "defect": defect.name,
            "severity": defect.severity,
        }

    rows.extend(duplicate_rows_to_add)
    _resolve_refund_links(rows, rng)  # re-resolve in case defects added refunds
    rng.shuffle(rows)

    return rows, manifest


def generate_benchmark_dataset(
    num_rows: int, seed: int, batch_id: str
) -> list[dict]:
    rng = random.Random(seed)
    batch_day = date.today()
    return _generate_clean_batch(num_rows, batch_id, batch_day, rng)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate transaction datasets.")
    parser.add_argument("--mode", choices=["seeded", "benchmark"], required=True)
    parser.add_argument("--rows", type=int, default=2000)
    parser.add_argument("--defect-rate", type=float, default=0.05)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--batch-id", default=None)
    args = parser.parse_args()

    batch_id = args.batch_id or f"BATCH-{date.today().isoformat()}"

    if args.mode == "seeded":
        rows, manifest = generate_seeded_dataset(
            args.rows, args.defect_rate, args.seed, batch_id
        )
        csv_path = DATA_DIR / "seeded_transactions.csv"
        manifest_path = DATA_DIR / "seeded_defect_manifest.json"
        _write_csv(rows, csv_path)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
        print(f"Wrote {len(rows)} rows -> {csv_path}")
        print(f"Wrote {len(manifest)} manifest entries -> {manifest_path}")
    else:
        rows = generate_benchmark_dataset(args.rows, args.seed, batch_id)
        csv_path = DATA_DIR / "benchmark_transactions.csv"
        _write_csv(rows, csv_path)
        print(f"Wrote {len(rows)} rows -> {csv_path}")


if __name__ == "__main__":
    main()
