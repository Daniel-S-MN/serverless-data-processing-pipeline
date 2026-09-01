"""
Tests for src/lambda/handler.py, using moto to mock S3/SNS rather
than manual stubs - this exercises more realistic AWS behavior
(actual bucket/key existence, real list_objects_v2 semantics) than
hand-rolled mocks would.

handler.py creates its boto3 clients AND reads SNS_TOPIC_ARN from the
environment at IMPORT time, not inside handler() - so the mock and
environment variables have to be set up BEFORE the module is
imported, then the module is reloaded fresh for each test via the
lambda_env fixture below. Without this, tests would silently run
against whatever was baked in the first time the module happened to
be imported.
"""

import csv
import importlib
import io
import json
import os
import sys

import boto3
import pytest
from moto import mock_aws

BUCKET = "test-transaction-bucket"
TOPIC_NAME = "test-rejected-files"


@pytest.fixture
def lambda_env():
    os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket=BUCKET)

        sns = boto3.client("sns", region_name="us-east-1")
        topic_arn = sns.create_topic(Name=TOPIC_NAME)["TopicArn"]

        # Subscribe an email so publishes have somewhere to "go" -
        # moto doesn't actually deliver email, but this mirrors the
        # real topic's shape and lets us assert on publish calls.
        sns.subscribe(TopicArn=topic_arn, Protocol="email", Endpoint="test@example.com")

        os.environ["SNS_TOPIC_ARN"] = topic_arn
        os.environ["PROCESSED_PREFIX"] = "processed/"
        os.environ["MAX_FILE_SIZE_MB"] = "25"

        # handler.py reads env vars and creates boto3 clients at
        # IMPORT time - reload so this test's mock/env are what it
        # actually picks up, not whatever was true the first time any
        # test file happened to import it.
        if "handler" in sys.modules:
            handler = importlib.reload(sys.modules["handler"])
        else:
            import handler

        yield {"s3": s3, "sns": sns, "bucket": BUCKET, "topic_arn": topic_arn, "handler": handler}


def _s3_event(key: str, size: int = 1000) -> dict:
    return {
        "Records": [{
            "s3": {
                "bucket": {"name": BUCKET},
                "object": {"key": key, "size": size},
            }
        }]
    }


def _put_csv(s3_client, key: str, rows: list[dict]):
    fieldnames = ["transaction_id", "partner_batch_id", "account_id", "transaction_date",
                  "posted_date", "transaction_type", "amount", "currency", "status",
                  "merchant_name", "related_transaction_id", "source_system"]
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    s3_client.put_object(Bucket=BUCKET, Key=key, Body=out.getvalue().encode("utf-8"))


class TestFilenameRejection:
    def test_bad_filename_publishes_rejection_alert(self, lambda_env, monkeypatch):
        published = []
        monkeypatch.setattr(
            lambda_env["handler"].sns_client, "publish",
            lambda **kwargs: published.append(kwargs) or {"MessageId": "fake"},
        )

        event = _s3_event("incoming/not_a_valid_filename.csv")
        result = lambda_env["handler"].handler(event, None)

        assert result["statusCode"] == 200
        assert len(published) == 1
        assert published[0]["Subject"] == "Transaction Pipeline: Rejected File"
        assert "not_a_valid_filename.csv" in published[0]["Message"]

    def test_bad_filename_does_not_write_to_processed(self, lambda_env, monkeypatch):
        monkeypatch.setattr(lambda_env["handler"].sns_client, "publish", lambda **kw: {"MessageId": "fake"})
        event = _s3_event("incoming/garbage.csv")
        lambda_env["handler"].handler(event, None)

        response = lambda_env["s3"].list_objects_v2(Bucket=BUCKET, Prefix="processed/")
        assert response.get("KeyCount", 0) == 0

    def test_valid_filename_does_not_trigger_rejection(self, lambda_env, monkeypatch):
        published = []
        monkeypatch.setattr(
            lambda_env["handler"].sns_client, "publish",
            lambda **kwargs: published.append(kwargs) or {"MessageId": "fake"},
        )
        key = "incoming/transactions_2026-09-01_BATCH-001.csv"
        _put_csv(lambda_env["s3"], key, [{
            "transaction_id": "TXN-1", "partner_batch_id": "BATCH-001", "account_id": "A1",
            "transaction_date": "2026-09-01", "posted_date": "2026-09-01",
            "transaction_type": "purchase", "amount": "25.00", "currency": "USD",
            "status": "completed", "merchant_name": "M", "related_transaction_id": "",
            "source_system": "S",
        }])
        lambda_env["handler"].handler(_s3_event(key), None)

        rejection_alerts = [p for p in published if p["Subject"] == "Transaction Pipeline: Rejected File"]
        assert rejection_alerts == []


class TestFileSizeLimit:
    def test_oversized_file_is_rejected(self, lambda_env, monkeypatch):
        published = []
        monkeypatch.setattr(
            lambda_env["handler"].sns_client, "publish",
            lambda **kwargs: published.append(kwargs) or {"MessageId": "fake"},
        )
        oversized_bytes = 26 * 1024 * 1024  # over the 25MB test limit
        key = "incoming/transactions_2026-09-01_BATCH-002.csv"
        event = _s3_event(key, size=oversized_bytes)

        result = lambda_env["handler"].handler(event, None)

        assert result["statusCode"] == 200
        assert len(published) == 1
        assert "exceeds" in published[0]["Message"]


class TestReprocessingDetection:
    def test_existing_output_triggers_reprocessing_alert(self, lambda_env, monkeypatch):
        published = []
        monkeypatch.setattr(
            lambda_env["handler"].sns_client, "publish",
            lambda **kwargs: published.append(kwargs) or {"MessageId": "fake"},
        )
        # Pre-populate processed/ output for this batch, simulating
        # an earlier successful run.
        lambda_env["s3"].put_object(
            Bucket=BUCKET, Key="processed/BATCH-003/summary.json", Body=b"{}",
        )

        key = "incoming/transactions_2026-09-01_BATCH-003.csv"
        _put_csv(lambda_env["s3"], key, [{
            "transaction_id": "TXN-1", "partner_batch_id": "BATCH-003", "account_id": "A1",
            "transaction_date": "2026-09-01", "posted_date": "2026-09-01",
            "transaction_type": "purchase", "amount": "25.00", "currency": "USD",
            "status": "completed", "merchant_name": "M", "related_transaction_id": "",
            "source_system": "S",
        }])
        lambda_env["handler"].handler(_s3_event(key), None)

        reprocessing_alerts = [p for p in published if p["Subject"] == "Transaction Pipeline: Reprocessing Detected"]
        assert len(reprocessing_alerts) == 1
        assert "BATCH-003" in reprocessing_alerts[0]["Message"]

    def test_reprocessing_still_writes_output(self, lambda_env, monkeypatch):
        """Reprocessing is a warning + alert, NOT a block - the write
        should still happen, overwriting the prior result."""
        monkeypatch.setattr(lambda_env["handler"].sns_client, "publish", lambda **kw: {"MessageId": "fake"})
        lambda_env["s3"].put_object(Bucket=BUCKET, Key="processed/BATCH-004/summary.json", Body=b"{}")

        key = "incoming/transactions_2026-09-01_BATCH-004.csv"
        _put_csv(lambda_env["s3"], key, [{
            "transaction_id": "TXN-1", "partner_batch_id": "BATCH-004", "account_id": "A1",
            "transaction_date": "2026-09-01", "posted_date": "2026-09-01",
            "transaction_type": "purchase", "amount": "25.00", "currency": "USD",
            "status": "completed", "merchant_name": "M", "related_transaction_id": "",
            "source_system": "S",
        }])
        lambda_env["handler"].handler(_s3_event(key), None)

        summary_obj = lambda_env["s3"].get_object(Bucket=BUCKET, Key="processed/BATCH-004/summary.json")
        summary = json.loads(summary_obj["Body"].read())
        assert summary["counts"]["total_rows"] == 1  # real content, not the pre-populated "{}"


class TestEndToEndWrite:
    def test_valid_batch_writes_transactions_and_summary(self, lambda_env, monkeypatch):
        monkeypatch.setattr(lambda_env["handler"].sns_client, "publish", lambda **kw: {"MessageId": "fake"})
        key = "incoming/transactions_2026-09-01_BATCH-005.csv"
        _put_csv(lambda_env["s3"], key, [
            {"transaction_id": "TXN-1", "partner_batch_id": "BATCH-005", "account_id": "A1",
             "transaction_date": "2026-09-01", "posted_date": "2026-09-01",
             "transaction_type": "purchase", "amount": "25.00", "currency": "USD",
             "status": "completed", "merchant_name": "M", "related_transaction_id": "",
             "source_system": "S"},
            {"transaction_id": "TXN-2", "partner_batch_id": "BATCH-005", "account_id": "",  # critical failure
             "transaction_date": "2026-09-01", "posted_date": "2026-09-01",
             "transaction_type": "purchase", "amount": "10.00", "currency": "USD",
             "status": "completed", "merchant_name": "M", "related_transaction_id": "",
             "source_system": "S"},
        ])
        lambda_env["handler"].handler(_s3_event(key), None)

        txns_obj = lambda_env["s3"].get_object(Bucket=BUCKET, Key="processed/BATCH-005/transactions.csv")
        txns_content = txns_obj["Body"].read().decode("utf-8")
        assert "TXN-1" in txns_content
        assert "TXN-2" not in txns_content  # rejected, excluded from output

        summary_obj = lambda_env["s3"].get_object(Bucket=BUCKET, Key="processed/BATCH-005/summary.json")
        summary = json.loads(summary_obj["Body"].read())
        assert summary["counts"]["passed"] == 1
        assert summary["counts"]["rejected_critical"] == 1
        assert summary["partner_batch_id"] == "BATCH-005"
        assert summary["source_key"] == key

    def test_all_rejected_batch_still_writes_empty_transactions_csv(self, lambda_env, monkeypatch):
        monkeypatch.setattr(lambda_env["handler"].sns_client, "publish", lambda **kw: {"MessageId": "fake"})
        key = "incoming/transactions_2026-09-01_BATCH-006.csv"
        _put_csv(lambda_env["s3"], key, [
            {"transaction_id": "TXN-1", "partner_batch_id": "BATCH-006", "account_id": "",
             "transaction_date": "2026-09-01", "posted_date": "2026-09-01",
             "transaction_type": "purchase", "amount": "10.00", "currency": "USD",
             "status": "completed", "merchant_name": "M", "related_transaction_id": "",
             "source_system": "S"},
        ])
        lambda_env["handler"].handler(_s3_event(key), None)

        # transactions.csv must still exist (header-only), per the
        # "consistent output shape" design decision.
        txns_obj = lambda_env["s3"].get_object(Bucket=BUCKET, Key="processed/BATCH-006/transactions.csv")
        rows = list(csv.DictReader(io.StringIO(txns_obj["Body"].read().decode("utf-8"))))
        assert rows == []


class TestDecodeFailure:
    def test_invalid_utf8_is_rejected_not_crashed(self, lambda_env, monkeypatch):
        published = []
        monkeypatch.setattr(
            lambda_env["handler"].sns_client, "publish",
            lambda **kwargs: published.append(kwargs) or {"MessageId": "fake"},
        )
        key = "incoming/transactions_2026-09-01_BATCH-007.csv"
        lambda_env["s3"].put_object(Bucket=BUCKET, Key=key, Body=b"\xff\xfe\x00invalid-utf8")

        result = lambda_env["handler"].handler(_s3_event(key), None)

        assert result["statusCode"] == 200  # did not crash
        rejection_alerts = [p for p in published if "UTF-8" in p["Message"]]
        assert len(rejection_alerts) == 1


class TestMultipleRecords:
    def test_multiple_records_in_one_event_are_each_processed(self, lambda_env, monkeypatch):
        monkeypatch.setattr(lambda_env["handler"].sns_client, "publish", lambda **kw: {"MessageId": "fake"})
        key_a = "incoming/transactions_2026-09-01_BATCH-008.csv"
        key_b = "incoming/transactions_2026-09-01_BATCH-009.csv"
        for key, batch in [(key_a, "BATCH-008"), (key_b, "BATCH-009")]:
            _put_csv(lambda_env["s3"], key, [{
                "transaction_id": "TXN-1", "partner_batch_id": batch, "account_id": "A1",
                "transaction_date": "2026-09-01", "posted_date": "2026-09-01",
                "transaction_type": "purchase", "amount": "25.00", "currency": "USD",
                "status": "completed", "merchant_name": "M", "related_transaction_id": "",
                "source_system": "S",
            }])

        event = {"Records": [
            {"s3": {"bucket": {"name": BUCKET}, "object": {"key": key_a, "size": 500}}},
            {"s3": {"bucket": {"name": BUCKET}, "object": {"key": key_b, "size": 500}}},
        ]}
        lambda_env["handler"].handler(event, None)

        for batch in ("BATCH-008", "BATCH-009"):
            obj = lambda_env["s3"].get_object(Bucket=BUCKET, Key=f"processed/{batch}/transactions.csv")
            assert obj["Body"].read()  # non-empty - each was processed independently
