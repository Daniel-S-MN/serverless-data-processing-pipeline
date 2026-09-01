"""
Polars implementation of the benchmark workload.

Same four conceptual stages as pandas_ops.py, written in Polars'
own idioms (expressions, lazy-friendly style) rather than a literal
translation of the Pandas version - the point of the benchmark is
each library doing the job the way it's meant to be used.
"""

from __future__ import annotations

from datetime import date

import polars as pl

REQUIRED_FIELDS = ["transaction_id", "account_id", "amount", "transaction_date"]
# Lists here, not sets - polars' .is_in() is documented and used with
# list-like inputs as the idiomatic convention (a set works in
# practice, but lists are what you'll see in polars code and docs).
# Compare to pandas_ops.py, which uses sets for the equivalent
# constants, matching pandas' own convention instead.
STATUSES = ["completed", "pending", "failed", "reversed"]
CURRENCIES = ["USD", "CAD", "EUR"]
POSITIVE_AMOUNT_TYPES = ["purchase", "fee"]
NEGATIVE_AMOUNT_TYPES = ["refund"]


def read(path: str) -> pl.DataFrame:
    return pl.read_csv(path, infer_schema_length=0)  # read all columns as strings, like pandas_ops


def parse(df: pl.DataFrame) -> pl.DataFrame:
    return df.with_columns(
        pl.col("amount").cast(pl.Float64, strict=False).alias("amount_parsed"),
        pl.col("transaction_date").str.to_date(strict=False).alias("transaction_date_parsed"),
        pl.col("posted_date").str.to_date(strict=False).alias("posted_date_parsed"),
    )


def validate(df: pl.DataFrame) -> pl.DataFrame:
    missing_required = pl.lit(False)
    for field in REQUIRED_FIELDS:
        missing_required = missing_required | pl.col(field).is_null() | (pl.col(field) == "")
    missing_required = missing_required | pl.col("amount_parsed").is_null() | pl.col("transaction_date_parsed").is_null()

    invalid_enum = ~pl.col("status").is_in(STATUSES) | ~pl.col("currency").is_in(CURRENCIES)

    date_logic_bad = (
        pl.col("posted_date_parsed").is_null()
        | (pl.col("posted_date_parsed") < pl.col("transaction_date_parsed"))
        | (pl.col("transaction_date_parsed") > pl.lit(date.today()))
    )

    amount_sign_mismatch = (
        (pl.col("transaction_type").is_in(POSITIVE_AMOUNT_TYPES) & (pl.col("amount_parsed") < 0))
        | (pl.col("transaction_type").is_in(NEGATIVE_AMOUNT_TYPES) & (pl.col("amount_parsed") > 0))
    )

    status_amount_bad = (pl.col("status") == "failed") & (pl.col("amount_parsed") != 0)
    zero_value = pl.col("amount_parsed") == 0

    df = df.with_columns(pl.col("transaction_id").count().over("transaction_id").alias("_id_count"))
    is_duplicate = pl.col("_id_count") > 1

    all_ids = set(df["transaction_id"].drop_nulls().to_list())
    is_refund = pl.col("transaction_type") == "refund"
    orphaned = is_refund & (
        pl.col("related_transaction_id").is_null()
        | ~pl.col("related_transaction_id").is_in(list(all_ids))
    )

    critical = missing_required | date_logic_bad | is_duplicate | orphaned
    warning = (invalid_enum | amount_sign_mismatch | status_amount_bad | zero_value) & ~critical

    return df.with_columns(critical.alias("critical"), warning.alias("warning"))


def aggregate(df: pl.DataFrame) -> dict:
    type_counts = df.group_by("transaction_type").len()
    by_type = dict(zip(type_counts["transaction_type"].to_list(), type_counts["len"].to_list()))
    return {
        "total_rows": df.height,
        "passed": int((~df["critical"]).sum()),
        "rejected_critical": int(df["critical"].sum()),
        "flagged_warning_only": int(df["warning"].sum()),
        "by_transaction_type": by_type,
    }


def run_full_workload(path: str) -> dict:
    df = read(path)
    df = parse(df)
    df = validate(df)
    return aggregate(df)
