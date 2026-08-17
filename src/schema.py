"""
Shared schema definitions for the transaction dataset.

Both the seeded (small, defect-labeled) generator and the benchmark
(large, mostly-clean) generator import from this module so the two
datasets never drift out of structural sync.
"""

from __future__ import annotations

# Column order used when writing CSV output. Keep this as the single
# source of truth for field order across generators and tests.
FIELDNAMES = [
    "transaction_id",
    "partner_batch_id",
    "account_id",
    "transaction_date",
    "posted_date",
    "transaction_type",
    "amount",
    "currency",
    "status",
    "merchant_name",
    "related_transaction_id",
    "source_system",
]

# Allowed enum values. Anything outside these sets is, by definition,
# an "invalid enum value" defect.
TRANSACTION_TYPES = ["purchase", "refund", "transfer", "adjustment", "fee"]
TRANSACTION_TYPE_WEIGHTS = [0.70, 0.12, 0.10, 0.05, 0.03]

STATUSES = ["completed", "pending", "failed", "reversed"]
STATUS_WEIGHTS = [0.90, 0.05, 0.03, 0.02]

CURRENCIES = ["USD", "CAD", "EUR"]
CURRENCY_WEIGHTS = [0.85, 0.10, 0.05]

MERCHANTS = [
    "Northwind Traders",
    "Contoso Retail",
    "Fabrikam Supply",
    "Globex Corp",
    "Initech Services",
    "Umbrella Logistics",
    "Wayne Hardware",
    "Stark Utilities",
    "Wonka Confections",
    "Acme Wholesale",
    "Pied Piper Software",
    "Hooli Cloud",
]

SOURCE_SYSTEMS = ["POS-EAST", "POS-WEST", "ONLINE-CHECKOUT", "ACH-BATCH"]

# Types where a positive amount is expected (money owed/charged).
POSITIVE_AMOUNT_TYPES = {"purchase", "fee"}
# Types where a negative amount is expected (money returned/credited).
NEGATIVE_AMOUNT_TYPES = {"refund"}
# Types where either sign is plausible (transfer, adjustment).
EITHER_SIGN_TYPES = {"transfer", "adjustment"}
