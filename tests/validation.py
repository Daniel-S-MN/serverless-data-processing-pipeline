"""
Transaction validation: structural gate, then business-rule checks.

Two passes, run in sequence per row:

  1. STRUCTURAL - can this row even be evaluated? Required fields
     present, amount parses as a number, dates parse as real dates.
     Always critical. A row that fails structurally skips business
     rules entirely - you can't check date logic on a date that
     doesn't parse.

  2. BUSINESS RULE - the 7 semantic/relationship checks from the
     dataset generator's defect taxonomy (schema.py's enums,
     duplicate/orphan detection, sign/status consistency), run only
     against rows that passed structurally. Each check carries the
     same severity (critical/warning) used when the synthetic dataset
     injects these same defects.

A row's overall disposition:
  - Any CRITICAL violation (structural or business-rule) -> rejected,
    excluded from valid output, included in the exceptions detail.
  - Only WARNING violations -> still passes, included in valid
    output, but the warnings are recorded in the exceptions detail
    too (not just counted) so a reviewer can see which rows were
    flagged, not just how many.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime

from schema import (
    STATUSES,
    CURRENCIES,
    POSITIVE_AMOUNT_TYPES,
    NEGATIVE_AMOUNT_TYPES,
)

REQUIRED_FIELDS = ["transaction_id", "account_id", "amount", "transaction_date"]


@dataclass
class Violation:
    rule: str
    severity: str  # "critical" | "warning"
    detail: str


@dataclass
class RowResult:
    transaction_id: str
    row: dict
    violations: list[Violation] = field(default_factory=list)

    @property
    def has_critical(self) -> bool:
        return any(v.severity == "critical" for v in self.violations)


def _parse_date(value: str):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def validate_structure(row: dict) -> tuple[dict | None, list[Violation]]:
    """
    Returns (parsed_row, violations). parsed_row is None if any
    required field is missing/unparseable - in that case business
    rules are never run for this row. parsed_row otherwise has
    amount as float and the two date fields as date objects, so
    business-rule checks don't need to re-parse anything.
    """
    violations: list[Violation] = []

    for f in REQUIRED_FIELDS:
        if not row.get(f):
            violations.append(
                Violation(
                    "missing_required_field",
                    "critical",
                    f"Required field '{f}' is blank.",
                )
            )

    if violations:
        return None, violations

    try:
        amount = float(row["amount"])
    except (ValueError, TypeError):
        violations.append(
            Violation(
                "missing_required_field",
                "critical",
                f"amount value '{row['amount']}' does not parse as a number.",
            )
        )
        return None, violations

    txn_date = _parse_date(row["transaction_date"])
    if txn_date is None:
        violations.append(
            Violation(
                "missing_required_field",
                "critical",
                f"transaction_date value '{row['transaction_date']}' is not a valid date.",
            )
        )
        return None, violations

    # posted_date is allowed to be blank/unparseable at the
    # structural level - date_logic_violation (a business rule)
    # handles a missing/bad posted_date as its own finding.
    posted_date = _parse_date(row.get("posted_date", ""))

    parsed = dict(row)
    parsed["amount"] = amount
    parsed["transaction_date"] = txn_date
    parsed["posted_date"] = posted_date
    return parsed, violations


def _check_invalid_enum(row: dict) -> Violation | None:
    if row["status"] not in STATUSES:
        return Violation("invalid_enum_value", "warning", f"status '{row['status']}' not recognized.")
    if row["currency"] not in CURRENCIES:
        return Violation("invalid_enum_value", "warning", f"currency '{row['currency']}' not recognized.")
    return None


def _check_date_logic(row: dict) -> Violation | None:
    if row["posted_date"] is None:
        return Violation("date_logic_violation", "critical", "posted_date is missing or unparseable.")
    if row["posted_date"] < row["transaction_date"]:
        return Violation("date_logic_violation", "critical", "posted_date precedes transaction_date.")
    if row["transaction_date"] > date.today():
        return Violation("date_logic_violation", "critical", "transaction_date is in the future.")
    return None


def _check_amount_sign(row: dict) -> Violation | None:
    txn_type = row["transaction_type"]
    amount = row["amount"]
    if txn_type in POSITIVE_AMOUNT_TYPES and amount < 0:
        return Violation("amount_sign_mismatch", "warning", f"Negative amount on {txn_type}.")
    if txn_type in NEGATIVE_AMOUNT_TYPES and amount > 0:
        return Violation("amount_sign_mismatch", "warning", f"Positive amount on {txn_type}.")
    return None


def _check_status_amount(row: dict) -> Violation | None:
    if row["status"] == "failed" and row["amount"] != 0:
        return Violation("status_amount_inconsistency", "warning", "status is 'failed' but amount is nonzero.")
    return None


def _check_zero_value(row: dict) -> Violation | None:
    if row["amount"] == 0:
        return Violation("zero_value_transaction", "warning", "amount is 0.00.")
    return None


def _check_business_rules(row: dict) -> list[Violation]:
    checks = [_check_invalid_enum, _check_date_logic, _check_amount_sign,
              _check_status_amount, _check_zero_value]
    return [v for v in (check(row) for check in checks) if v is not None]


def _check_file_level(parsed_rows: list[dict], all_raw_ids: set[str]) -> dict[str, list[Violation]]:
    """
    Returns transaction_id -> extra violations, for the two checks
    that need the whole batch rather than a single row:
    duplicate_transaction and orphaned_refund.

    all_raw_ids is EVERY transaction_id present in the raw file,
    including rows that failed structural validation - a refund
    pointing at a real transaction_id shouldn't be flagged as
    orphaned just because that other row happened to also have a
    blank account_id. Structural validity and "does this ID exist in
    the file" are separate questions.
    """
    extra: dict[str, list[Violation]] = {}
    id_counts = Counter(r["transaction_id"] for r in parsed_rows)
    seen_dup_ids: set[str] = set()

    for row in parsed_rows:
        tid = row["transaction_id"]
        if id_counts[tid] > 1 and tid not in seen_dup_ids:
            extra.setdefault(tid, []).append(
                Violation("duplicate_transaction", "critical",
                           f"transaction_id appears {id_counts[tid]} times in the file.")
            )
            seen_dup_ids.add(tid)
        if row["transaction_type"] == "refund":
            related = row.get("related_transaction_id")
            if not related or related not in all_raw_ids:
                extra.setdefault(tid, []).append(
                    Violation("orphaned_refund", "critical",
                               f"related_transaction_id '{related}' not found in file.")
                )
    return extra


def validate_batch(rows: list[dict]) -> tuple[list[dict], list[RowResult], dict]:
    """
    Runs structural + business-rule validation over an entire batch.

    Returns:
      valid_rows      - original (unparsed) row dicts that passed
                         (no critical violations); this is what gets
                         written to transactions.csv.
      exception_rows  - RowResult for every row with ANY violation
                         (critical or warning-only) - this is what
                         gets written to summary.json's detail.
      summary         - aggregate counts for the top of summary.json.
    """
    results: list[RowResult] = []
    parsed_rows: list[dict] = []
    raw_by_parsed_index: list[dict] = []  # raw row paired 1:1 with parsed_rows, by position
    structural_failures: list[RowResult] = []
    all_raw_ids = {row.get("transaction_id") for row in rows if row.get("transaction_id")}

    for row in rows:
        parsed, struct_violations = validate_structure(row)
        tid = row.get("transaction_id") or "UNKNOWN"
        if parsed is None:
            structural_failures.append(RowResult(tid, row, struct_violations))
        else:
            parsed_rows.append(parsed)
            raw_by_parsed_index.append(row)  # original raw dict, unparsed

    file_level = _check_file_level(parsed_rows, all_raw_ids)

    for parsed, raw_row in zip(parsed_rows, raw_by_parsed_index):
        tid = parsed["transaction_id"]
        violations = _check_business_rules(parsed) + file_level.get(tid, [])
        # RowResult.row holds the ORIGINAL raw row (unparsed strings),
        # not the typed `parsed` dict used for rule evaluation - this
        # is what actually gets written to transactions.csv, and
        # needs to preserve source formatting (e.g. "25.00", not the
        # float 25.0 parsing would otherwise produce).
        results.append(RowResult(tid, raw_row, violations))

    all_results = structural_failures + results
    valid_rows = [r.row for r in all_results if not r.has_critical]
    exception_rows = [r for r in all_results if r.violations]

    summary = {
        "total_rows": len(rows),
        "passed": len(valid_rows),
        "rejected_critical": sum(1 for r in all_results if r.has_critical),
        "flagged_warning_only": sum(
            1 for r in all_results if r.violations and not r.has_critical
        ),
    }

    return valid_rows, exception_rows, summary
