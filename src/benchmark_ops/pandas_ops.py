"""
Pandas implementation of the benchmark workload.

Four stages, timed and measured separately by the orchestrator in
scripts/benchmark.py. This mirrors the CONCEPTUAL stages
validation.py performs (structural gate, business-rule taxonomy,
file-level checks, aggregate summary) - not its literal code, since
validation.py is deliberately plain Python/stdlib. This is Pandas
doing the same job in Pandas' own idioms.
"""

from __future__ import annotations

import pandas as pd

REQUIRED_FIELDS = ["transaction_id", "account_id", "amount", "transaction_date"]
# Sets here, not lists - pandas' .isin() accepts either, but a set
# gives faster O(1) membership checks and is the more idiomatic
# choice in pandas code. (Compare to polars_ops.py, which uses lists
# for the equivalent constants - see the comment there for why.)
STATUSES = {"completed", "pending", "failed", "reversed"}
CURRENCIES = {"USD", "CAD", "EUR"}
POSITIVE_AMOUNT_TYPES = {"purchase", "fee"}
NEGATIVE_AMOUNT_TYPES = {"refund"}


def read(path: str) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str)


def parse(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["amount_parsed"] = pd.to_numeric(df["amount"], errors="coerce")
    df["transaction_date_parsed"] = pd.to_datetime(df["transaction_date"], errors="coerce")
    df["posted_date_parsed"] = pd.to_datetime(df["posted_date"], errors="coerce")
    return df


def validate(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    missing_required = pd.Series(False, index=df.index)
    for field in REQUIRED_FIELDS:
        missing_required |= df[field].isna() | (df[field] == "")
    missing_required |= df["amount_parsed"].isna()
    missing_required |= df["transaction_date_parsed"].isna()

    invalid_enum = ~df["status"].isin(STATUSES) | ~df["currency"].isin(CURRENCIES)

    date_logic_bad = (
        df["posted_date_parsed"].isna()
        | (df["posted_date_parsed"] < df["transaction_date_parsed"])
        | (df["transaction_date_parsed"] > pd.Timestamp.today())
    )

    positive_mask = df["transaction_type"].isin(POSITIVE_AMOUNT_TYPES) & (df["amount_parsed"] < 0)
    negative_mask = df["transaction_type"].isin(NEGATIVE_AMOUNT_TYPES) & (df["amount_parsed"] > 0)
    amount_sign_mismatch = positive_mask | negative_mask

    status_amount_bad = (df["status"] == "failed") & (df["amount_parsed"] != 0)
    zero_value = df["amount_parsed"] == 0

    dup_counts = df["transaction_id"].value_counts()
    is_duplicate = df["transaction_id"].map(dup_counts).fillna(0) > 1

    all_ids = set(df["transaction_id"].dropna())
    is_refund = df["transaction_type"] == "refund"
    orphaned = is_refund & (~df["related_transaction_id"].isin(all_ids) | df["related_transaction_id"].isna())

    critical = missing_required | date_logic_bad | is_duplicate | orphaned
    warning = invalid_enum | amount_sign_mismatch | status_amount_bad | zero_value

    df["critical"] = critical
    df["warning"] = warning & ~critical
    return df


def aggregate(df: pd.DataFrame) -> dict:
    return {
        "total_rows": len(df),
        "passed": int((~df["critical"]).sum()),
        "rejected_critical": int(df["critical"].sum()),
        "flagged_warning_only": int(df["warning"].sum()),
        "by_transaction_type": df["transaction_type"].value_counts().to_dict(),
    }


def run_full_workload(path: str) -> dict:
    df = read(path)
    df = parse(df)
    df = validate(df)
    return aggregate(df)
