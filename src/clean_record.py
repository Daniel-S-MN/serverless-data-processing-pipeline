"""
Generates a single fully valid transaction record.

Design notes (see project README for the full rationale):
- Field generation is sequenced because fields depend on each other:
  transaction_type is picked first, then amount sign/magnitude and
  related_transaction_id are derived from it.
- related_transaction_id is NOT resolved here. A refund's related ID
  must point at a real transaction_id, which may not exist yet if
  we're generating rows one at a time. Refund linkage is resolved in
  a second pass by the caller (see generate_dataset.py), after a full
  pool of non-refund transaction_ids exists to choose from.
- Distributions are weighted, not uniform, so the dataset doesn't
  look artificially random (e.g. 90% of transactions "completed",
  not an even split across every status).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, asdict
from datetime import date, timedelta

from schema import (
    TRANSACTION_TYPES,
    TRANSACTION_TYPE_WEIGHTS,
    STATUSES,
    STATUS_WEIGHTS,
    CURRENCIES,
    CURRENCY_WEIGHTS,
    MERCHANTS,
    SOURCE_SYSTEMS,
    POSITIVE_AMOUNT_TYPES,
    NEGATIVE_AMOUNT_TYPES,
)


@dataclass
class TransactionRecord:
    transaction_id: str
    partner_batch_id: str
    account_id: str
    transaction_date: str
    posted_date: str
    transaction_type: str
    amount: str
    currency: str
    status: str
    merchant_name: str
    related_transaction_id: str
    source_system: str

    def to_dict(self) -> dict:
        return asdict(self)


def _random_amount(transaction_type: str, rng: random.Random) -> float:
    """
    Log-normal magnitude so most amounts are small-dollar with a
    realistic long tail toward larger ones. Sign is derived from
    transaction_type rather than chosen independently.
    """
    magnitude = round(rng.lognormvariate(mu=3.2, sigma=0.9), 2)
    magnitude = max(0.01, min(magnitude, 9999.99))

    if transaction_type in NEGATIVE_AMOUNT_TYPES:
        return -magnitude
    if transaction_type in POSITIVE_AMOUNT_TYPES:
        return magnitude
    # transfer / adjustment: either sign is plausible
    return magnitude if rng.random() < 0.5 else -magnitude


def _random_dates(batch_day: date, rng: random.Random) -> tuple[str, str]:
    """
    transaction_date lands on (or shortly before) the batch day.
    posted_date settles 0-2 days after transaction_date, never before.
    """
    txn_date = batch_day - timedelta(days=rng.randint(0, 1))
    settle_lag = rng.choices([0, 1, 2], weights=[0.6, 0.3, 0.1])[0]
    posted_date = txn_date + timedelta(days=settle_lag)
    return txn_date.isoformat(), posted_date.isoformat()


def generate_clean_record(
    row_index: int,
    batch_id: str,
    batch_day: date,
    rng: random.Random,
) -> TransactionRecord:
    """
    Produce one fully valid TransactionRecord. related_transaction_id
    is left as an empty string here for every row; refund linkage is
    resolved afterward by the caller once the full batch exists.
    """
    transaction_type = rng.choices(
        TRANSACTION_TYPES, weights=TRANSACTION_TYPE_WEIGHTS
    )[0]
    status = rng.choices(STATUSES, weights=STATUS_WEIGHTS)[0]
    currency = rng.choices(CURRENCIES, weights=CURRENCY_WEIGHTS)[0]
    txn_date, posted_date = _random_dates(batch_day, rng)
    amount = _random_amount(transaction_type, rng)

    # A failed/reversed transaction shouldn't have settled money moved.
    if status in ("failed", "reversed"):
        amount = 0.0 if status == "failed" else amount

    return TransactionRecord(
        transaction_id=f"TXN-{row_index:08d}",
        partner_batch_id=batch_id,
        account_id=f"ACCT-{rng.randint(1, 5000):06d}",
        transaction_date=txn_date,
        posted_date=posted_date,
        transaction_type=transaction_type,
        amount=f"{amount:.2f}",
        currency=currency,
        status=status,
        merchant_name=rng.choice(MERCHANTS),
        related_transaction_id="",
        source_system=rng.choice(SOURCE_SYSTEMS),
    )
