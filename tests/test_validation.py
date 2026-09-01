"""
Tests for src/lambda/validation.py.

Organized to mirror validation.py's own structure: structural checks,
then each business rule individually (both triggering AND not
triggering - a check that's only ever tested on failure is half
tested), then the two file-level checks (including explicit
regression tests for the two bugs found during manual testing), then
overall disposition and summary counts.
"""

from validation import validate_structure, validate_batch


# --- Structural validation ---

class TestStructuralValidation:
    def test_valid_row_parses_cleanly(self, valid_row):
        parsed, violations = validate_structure(valid_row)
        assert parsed is not None
        assert violations == []
        assert isinstance(parsed["amount"], float)

    def test_missing_account_id_is_critical(self, valid_row):
        valid_row["account_id"] = ""
        parsed, violations = validate_structure(valid_row)
        assert parsed is None
        assert len(violations) == 1
        assert violations[0].rule == "missing_required_field"
        assert violations[0].severity == "critical"

    def test_missing_amount_is_critical(self, valid_row):
        valid_row["amount"] = ""
        parsed, violations = validate_structure(valid_row)
        assert parsed is None
        assert violations[0].rule == "missing_required_field"

    def test_missing_transaction_date_is_critical(self, valid_row):
        valid_row["transaction_date"] = ""
        parsed, violations = validate_structure(valid_row)
        assert parsed is None

    def test_unparseable_amount_is_critical(self, valid_row):
        valid_row["amount"] = "not-a-number"
        parsed, violations = validate_structure(valid_row)
        assert parsed is None
        assert violations[0].rule == "missing_required_field"

    def test_unparseable_transaction_date_is_critical(self, valid_row):
        valid_row["transaction_date"] = "not-a-date"
        parsed, violations = validate_structure(valid_row)
        assert parsed is None

    def test_missing_posted_date_does_not_fail_structurally(self, valid_row):
        """posted_date is checked at the business-rule level
        (date_logic_violation), not structurally - a missing/bad
        posted_date should still let the row parse."""
        valid_row["posted_date"] = ""
        parsed, violations = validate_structure(valid_row)
        assert parsed is not None
        assert violations == []
        assert parsed["posted_date"] is None


# --- Business rules: each tested triggering AND not triggering ---

class TestInvalidEnumValue:
    def test_valid_status_and_currency_pass(self, valid_row):
        _, exceptions, _ = validate_batch([valid_row])
        assert exceptions == []

    def test_invalid_status_flagged_as_warning(self, valid_row):
        valid_row["status"] = "not_a_real_status"
        valid_rows, exceptions, _ = validate_batch([valid_row])
        assert len(exceptions) == 1
        assert exceptions[0].violations[0].rule == "invalid_enum_value"
        assert exceptions[0].violations[0].severity == "warning"
        assert valid_row in valid_rows  # warning-only still passes

    def test_invalid_currency_flagged_as_warning(self, valid_row):
        valid_row["currency"] = "XXX"
        _, exceptions, _ = validate_batch([valid_row])
        assert exceptions[0].violations[0].rule == "invalid_enum_value"


class TestDateLogicViolation:
    def test_posted_before_transaction_date_is_critical(self, valid_row, yesterday):
        valid_row["posted_date"] = yesterday
        valid_rows, exceptions, _ = validate_batch([valid_row])
        assert len(exceptions) == 1
        assert exceptions[0].violations[0].rule == "date_logic_violation"
        assert exceptions[0].violations[0].severity == "critical"
        assert valid_rows == []

    def test_transaction_date_in_future_is_critical(self, valid_row, tomorrow):
        valid_row["transaction_date"] = tomorrow
        valid_row["posted_date"] = tomorrow
        valid_rows, exceptions, _ = validate_batch([valid_row])
        assert exceptions[0].violations[0].rule == "date_logic_violation"
        assert valid_rows == []

    def test_same_day_dates_are_fine(self, valid_row):
        _, exceptions, _ = validate_batch([valid_row])
        assert exceptions == []


class TestAmountSignMismatch:
    def test_negative_amount_on_purchase_is_warning(self, valid_row):
        valid_row["transaction_type"] = "purchase"
        valid_row["amount"] = "-25.00"
        valid_rows, exceptions, _ = validate_batch([valid_row])
        assert exceptions[0].violations[0].rule == "amount_sign_mismatch"
        assert exceptions[0].violations[0].severity == "warning"
        assert valid_row in valid_rows

    def test_positive_amount_on_refund_is_warning(self, valid_row):
        valid_row["transaction_type"] = "refund"
        valid_row["amount"] = "25.00"
        _, exceptions, _ = validate_batch([valid_row])
        assert exceptions[0].violations[0].rule == "amount_sign_mismatch"

    def test_positive_purchase_amount_is_fine(self, valid_row):
        _, exceptions, _ = validate_batch([valid_row])
        assert exceptions == []

    def test_either_sign_allowed_on_transfer(self, valid_row):
        valid_row["transaction_type"] = "transfer"
        valid_row["amount"] = "-25.00"
        _, exceptions, _ = validate_batch([valid_row])
        assert not any(v.rule == "amount_sign_mismatch" for r in exceptions for v in r.violations)


class TestStatusAmountInconsistency:
    def test_failed_status_with_nonzero_amount_is_warning(self, valid_row):
        valid_row["status"] = "failed"
        valid_row["amount"] = "25.00"
        _, exceptions, _ = validate_batch([valid_row])
        assert exceptions[0].violations[0].rule == "status_amount_inconsistency"
        assert exceptions[0].violations[0].severity == "warning"

    def test_failed_status_with_zero_amount_is_fine_for_this_rule(self, valid_row):
        valid_row["status"] = "failed"
        valid_row["amount"] = "0.00"
        _, exceptions, _ = validate_batch([valid_row])
        rules = [v.rule for r in exceptions for v in r.violations]
        assert "status_amount_inconsistency" not in rules
        # zero_value_transaction still fires though - by design, see
        # README's "Validation Logic" section on this exact overlap.
        assert "zero_value_transaction" in rules


class TestZeroValueTransaction:
    def test_zero_amount_is_warning(self, valid_row):
        valid_row["amount"] = "0.00"
        _, exceptions, _ = validate_batch([valid_row])
        assert exceptions[0].violations[0].rule == "zero_value_transaction"
        assert exceptions[0].violations[0].severity == "warning"

    def test_nonzero_amount_is_fine(self, valid_row):
        _, exceptions, _ = validate_batch([valid_row])
        assert exceptions == []


# --- File-level checks, including regression tests for fixed bugs ---

class TestDuplicateTransaction:
    def test_duplicate_id_flags_both_rows_critical(self, valid_row):
        row2 = dict(valid_row)  # same transaction_id
        valid_rows, exceptions, summary = validate_batch([valid_row, row2])
        assert len(exceptions) == 2
        assert valid_rows == []
        assert summary["rejected_critical"] == 2

    def test_duplicate_violation_appears_exactly_once_per_row(self, valid_row):
        """Regression test: an earlier bug caused the violation to be
        appended once per row SCANNED that shared the ID, so a pair
        of duplicates ended up with the violation listed twice each,
        not once."""
        row2 = dict(valid_row)
        _, exceptions, _ = validate_batch([valid_row, row2])
        for result in exceptions:
            dup_violations = [v for v in result.violations if v.rule == "duplicate_transaction"]
            assert len(dup_violations) == 1

    def test_unique_ids_are_not_flagged_as_duplicates(self, valid_row):
        row2 = dict(valid_row)
        row2["transaction_id"] = "TXN-00000002"
        _, exceptions, _ = validate_batch([valid_row, row2])
        assert exceptions == []


class TestOrphanedRefund:
    def test_refund_pointing_to_real_transaction_is_fine(self, valid_row):
        original = dict(valid_row)
        original["transaction_id"] = "TXN-ORIGINAL"

        refund = dict(valid_row)
        refund["transaction_id"] = "TXN-REFUND"
        refund["transaction_type"] = "refund"
        refund["amount"] = "-25.00"
        refund["related_transaction_id"] = "TXN-ORIGINAL"

        _, exceptions, _ = validate_batch([original, refund])
        assert exceptions == []

    def test_refund_pointing_to_nonexistent_id_is_critical(self, valid_row):
        valid_row["transaction_type"] = "refund"
        valid_row["amount"] = "-25.00"
        valid_row["related_transaction_id"] = "TXN-DOES-NOT-EXIST"
        valid_rows, exceptions, _ = validate_batch([valid_row])
        assert exceptions[0].violations[0].rule == "orphaned_refund"
        assert exceptions[0].violations[0].severity == "critical"
        assert valid_rows == []

    def test_refund_target_that_failed_structurally_is_not_orphaned(self, valid_row):
        """Regression test: an earlier bug only considered
        transaction_ids among rows that PASSED structural validation
        when checking for orphans - so a legitimate refund pointing
        at a real ID could be wrongly flagged if that other row
        happened to fail structurally for an unrelated reason (e.g. a
        blank account_id). The orphan check must consider every raw
        ID in the file, independent of that row's own validity."""
        broken_original = dict(valid_row)
        broken_original["transaction_id"] = "TXN-ORIGINAL"
        broken_original["account_id"] = ""  # fails structurally

        refund = dict(valid_row)
        refund["transaction_id"] = "TXN-REFUND"
        refund["transaction_type"] = "refund"
        refund["amount"] = "-25.00"
        refund["related_transaction_id"] = "TXN-ORIGINAL"

        _, exceptions, _ = validate_batch([broken_original, refund])
        # If the row is fully clean it won't appear in exceptions at
        # all (validate_batch only includes rows with violations) -
        # so "no orphaned_refund violation recorded for TXN-REFUND"
        # is the correct assertion, not "TXN-REFUND must appear in
        # exceptions."
        orphan_violations_for_refund = [
            v for r in exceptions if r.transaction_id == "TXN-REFUND"
            for v in r.violations if v.rule == "orphaned_refund"
        ]
        assert orphan_violations_for_refund == []


# --- Overall disposition and summary ---

class TestDispositionAndSummary:
    def test_warning_only_row_still_passes(self, valid_row):
        valid_row["amount"] = "0.00"  # zero_value_transaction, warning only
        valid_rows, exceptions, summary = validate_batch([valid_row])
        assert valid_row in valid_rows
        assert len(exceptions) == 1  # still recorded in exception detail
        assert summary["passed"] == 1
        assert summary["flagged_warning_only"] == 1
        assert summary["rejected_critical"] == 0

    def test_critical_row_is_excluded_from_valid_rows(self, valid_row):
        valid_row["account_id"] = ""
        valid_rows, exceptions, summary = validate_batch([valid_row])
        assert valid_rows == []
        assert summary["rejected_critical"] == 1
        assert summary["passed"] == 0

    def test_summary_counts_are_internally_consistent(self, valid_row):
        clean = dict(valid_row)
        clean["transaction_id"] = "TXN-CLEAN"

        warning_only = dict(valid_row)
        warning_only["transaction_id"] = "TXN-WARN"
        warning_only["amount"] = "0.00"

        critical = dict(valid_row)
        critical["transaction_id"] = "TXN-CRIT"
        critical["account_id"] = ""

        valid_rows, exceptions, summary = validate_batch([clean, warning_only, critical])
        assert summary["total_rows"] == 3
        assert summary["passed"] == len(valid_rows) == 2
        assert summary["rejected_critical"] == 1
        assert summary["flagged_warning_only"] == 1
