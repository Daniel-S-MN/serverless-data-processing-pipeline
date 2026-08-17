"""
Defect taxonomy for the seeded dataset.

Each defect is registered as a (name, severity, mutation function)
tuple rather than hardcoded as separate branches elsewhere. To add a
9th defect type later, add one entry to DEFECT_REGISTRY - nothing
else in the generator needs to change.

Mutation functions take a clean row dict and a random-number pool
context (other valid transaction_ids already generated, where
relevant) and return a mutated copy. They must not mutate in place.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Callable

from schema import CURRENCIES, STATUSES


@dataclass(frozen=True)
class Defect:
    name: str
    severity: str  # "critical" | "warning"
    description: str
    apply: Callable[[dict, random.Random], dict]


def _missing_required_field(row: dict, rng: random.Random) -> dict:
    row = dict(row)
    field = rng.choice(["account_id", "amount", "transaction_date"])
    row[field] = ""
    return row


def _duplicate_transaction(row: dict, rng: random.Random) -> dict:
    # Marked here; actual duplication (re-inserting the row a second
    # time under a possibly different batch_id) happens in the
    # generator once it has the full pool of rows to duplicate from.
    row = dict(row)
    row["_defect_duplicate"] = True
    return row


def _invalid_enum_value(row: dict, rng: random.Random) -> dict:
    row = dict(row)
    if rng.random() < 0.5:
        row["status"] = "unknown_status_" + str(rng.randint(1, 99))
    else:
        row["currency"] = rng.choice(["XXX", "ZZZ", "N/A"])
    return row


def _date_logic_violation(row: dict, rng: random.Random) -> dict:
    row = dict(row)
    if rng.random() < 0.5:
        # posted_date before transaction_date
        row["posted_date"] = row["transaction_date"]
        row["transaction_date"] = _shift_date(row["transaction_date"], days=+3)
    else:
        # transaction_date in the future
        row["transaction_date"] = _shift_date(row["transaction_date"], days=+365)
    return row


def _orphaned_refund(row: dict, rng: random.Random) -> dict:
    row = dict(row)
    row["transaction_type"] = "refund"
    row["amount"] = f"-{abs(float(row['amount'] or 1)):.2f}"
    row["related_transaction_id"] = f"TXN-{rng.randint(90000000, 99999999)}"
    return row


def _amount_sign_mismatch(row: dict, rng: random.Random) -> dict:
    row = dict(row)
    try:
        amt = float(row["amount"])
    except (ValueError, TypeError):
        amt = 10.0
    row["amount"] = f"{-amt:.2f}" if row["transaction_type"] in (
        "purchase",
        "fee",
    ) else f"{abs(amt):.2f}"
    return row


def _status_amount_inconsistency(row: dict, rng: random.Random) -> dict:
    row = dict(row)
    row["status"] = "failed"
    row["amount"] = f"{abs(float(row['amount'] or 10)):.2f}"
    return row


def _zero_value_transaction(row: dict, rng: random.Random) -> dict:
    row = dict(row)
    row["amount"] = "0.00"
    return row


def _shift_date(iso_date: str, days: int) -> str:
    from datetime import date, timedelta

    y, m, d = (int(part) for part in iso_date.split("-"))
    return (date(y, m, d) + timedelta(days=days)).isoformat()


DEFECT_REGISTRY: list[Defect] = [
    Defect(
        "missing_required_field",
        "critical",
        "A required field (account_id, amount, or transaction_date) is blank.",
        _missing_required_field,
    ),
    Defect(
        "duplicate_transaction",
        "critical",
        "The same transaction_id appears more than once in the file.",
        _duplicate_transaction,
    ),
    Defect(
        "invalid_enum_value",
        "warning",
        "status or currency falls outside the allowed set.",
        _invalid_enum_value,
    ),
    Defect(
        "date_logic_violation",
        "critical",
        "posted_date precedes transaction_date, or transaction_date is in the future.",
        _date_logic_violation,
    ),
    Defect(
        "orphaned_refund",
        "critical",
        "related_transaction_id does not match any transaction_id in the file.",
        _orphaned_refund,
    ),
    Defect(
        "amount_sign_mismatch",
        "warning",
        "Sign of amount is inconsistent with transaction_type.",
        _amount_sign_mismatch,
    ),
    Defect(
        "status_amount_inconsistency",
        "warning",
        "status is 'failed' but amount is nonzero.",
        _status_amount_inconsistency,
    ),
    Defect(
        "zero_value_transaction",
        "warning",
        "amount is exactly 0.00.",
        _zero_value_transaction,
    ),
]

DEFECT_BY_NAME = {d.name: d for d in DEFECT_REGISTRY}
