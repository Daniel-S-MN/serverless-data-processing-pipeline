"""
Shared fixtures for the test suite.
"""

import sys
from pathlib import Path

# Both src/ (for schema.py) and src/lambda/ (for validation.py,
# handler.py) need to be importable - same PYTHONPATH situation
# documented in the README's "Running Code Locally" section, just
# handled here so `pytest` works without extra setup.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "src" / "lambda"))

import pytest
from datetime import date, timedelta


@pytest.fixture
def valid_row():
    """
    A single fully valid row, matching every structural and
    business-rule check. Tests mutate ONE field off this baseline to
    isolate exactly what they're checking - if a test fails, the
    change you made is the only thing that could be wrong.
    """
    today = date.today().isoformat()
    return {
        "transaction_id": "TXN-00000001",
        "partner_batch_id": "BATCH-TEST",
        "account_id": "ACCT-000001",
        "transaction_date": today,
        "posted_date": today,
        "transaction_type": "purchase",
        "amount": "25.00",
        "currency": "USD",
        "status": "completed",
        "merchant_name": "Test Merchant",
        "related_transaction_id": "",
        "source_system": "TEST-SYS",
    }


@pytest.fixture
def yesterday():
    return (date.today() - timedelta(days=1)).isoformat()


@pytest.fixture
def tomorrow():
    return (date.today() + timedelta(days=1)).isoformat()
