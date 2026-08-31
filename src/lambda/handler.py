"""
Lambda handler for the S3-triggered transaction pipeline.

Per incoming object:
  1. Validate the filename convention. No match -> reject + SNS
     alert, file left untouched.
  2. Check the object size against MAX_FILE_SIZE_MB (read from the S3
     event itself, no extra API call needed). Oversized -> reject +
     SNS alert, same as a bad filename - an oversized daily file is
     itself an anomaly worth a human looking at, not something this
     Lambda should try to force through.
  3. Check the filename's embedded date for staleness. WARNING only -
     does not block. Late/backfilled files are legitimate.
  4. Check whether processed/{partner_batch_id}/ already has output.
     If so, this is a reprocessing event - WARNING + SNS alert, but
     still allowed to proceed; the write step below will overwrite
     the prior result, deliberately and auditably.
  5. Read the CSV from S3, STREAMING line-by-line (iter_lines) rather
     than loading the whole body into memory at once.
  6. Run validate_batch() (structural gate + business-rule taxonomy).
  7. Write processed/{batch_id}/transactions.csv (valid rows), then
     processed/{batch_id}/summary.json (counts + exception detail)
     LAST - summary.json's presence is the "this batch finished"
     signal the reprocessing check above relies on, so if the Lambda
     crashes mid-write, the worst case is a stale-but-present
     summary.json next to a newer transactions.csv, not the reverse.
"""

import csv
import io
import json
import logging
import os
import re
from datetime import date, datetime, timezone

import boto3

from validation import validate_batch

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3_client = boto3.client("s3")
sns_client = boto3.client("sns")

SNS_TOPIC_ARN = os.environ.get("SNS_TOPIC_ARN")
PROCESSED_PREFIX = os.environ.get("PROCESSED_PREFIX", "processed/")
MAX_FILE_SIZE_BYTES = int(os.environ.get("MAX_FILE_SIZE_MB", "25")) * 1024 * 1024

FIELDNAMES = [
    "transaction_id", "partner_batch_id", "account_id", "transaction_date",
    "posted_date", "transaction_type", "amount", "currency", "status",
    "merchant_name", "related_transaction_id", "source_system",
]

# transactions_2026-08-20_BATCH-001.csv -> ("2026-08-20", "BATCH-001")
FILENAME_PATTERN = re.compile(
    r"^transactions_(\d{4}-\d{2}-\d{2})_([A-Za-z0-9\-]+)\.csv$"
)

STALENESS_THRESHOLD_DAYS = 3


def _parse_filename(key: str):
    filename = key.split("/")[-1]
    match = FILENAME_PATTERN.match(filename)
    if not match:
        return None
    return match.group(1), match.group(2)


def _check_staleness(date_str: str) -> str | None:
    filename_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    delta_days = (date.today() - filename_date).days
    if delta_days > STALENESS_THRESHOLD_DAYS:
        return f"Filename date {date_str} is {delta_days} days in the past"
    if delta_days < -STALENESS_THRESHOLD_DAYS:
        return f"Filename date {date_str} is {abs(delta_days)} days in the future"
    return None


def _is_reprocessing(bucket: str, partner_batch_id: str) -> bool:
    prefix = f"{PROCESSED_PREFIX}{partner_batch_id}/"
    response = s3_client.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=1)
    return response.get("KeyCount", 0) > 0


def _publish_alert(subject: str, message: str, bucket: str, key: str) -> None:
    if not SNS_TOPIC_ARN:
        logger.error("SNS_TOPIC_ARN not set - cannot send alert for %s/%s", bucket, key)
        return
    sns_client.publish(TopicArn=SNS_TOPIC_ARN, Subject=subject, Message=message)
    logger.info("Alert published (%s) for bucket=%s key=%s", subject, bucket, key)


def _send_rejection_alert(bucket: str, key: str, reason: str) -> None:
    message = (
        f"A file was rejected: {reason}\n\n"
        f"Bucket: {bucket}\n"
        f"Key: {key}\n\n"
        f"The file has been left in place. Review it manually in the AWS "
        f"Console and rename, move, or delete it as appropriate."
    )
    _publish_alert("Transaction Pipeline: Rejected File", message, bucket, key)


def _send_reprocessing_alert(bucket: str, key: str, partner_batch_id: str) -> None:
    message = (
        f"A file was received for a batch that has already been processed. "
        f"The previous result will be overwritten.\n\n"
        f"Bucket: {bucket}\n"
        f"Key: {key}\n"
        f"Partner batch ID: {partner_batch_id}\n\n"
        f"No action is required unless this reprocessing was unexpected."
    )
    _publish_alert("Transaction Pipeline: Reprocessing Detected", message, bucket, key)


def _read_csv_rows(bucket: str, key: str) -> list[dict]:
    """
    Streams the object line-by-line rather than loading the whole
    body into memory at once - keeps memory usage proportional to
    what's being processed at any moment, not the full file size.
    """
    response = s3_client.get_object(Bucket=bucket, Key=key)
    lines = (line.decode("utf-8") for line in response["Body"].iter_lines())
    return list(csv.DictReader(lines))


def _write_output(bucket: str, partner_batch_id: str, valid_rows: list[dict],
                   exception_rows, summary: dict, source_key: str) -> None:
    prefix = f"{PROCESSED_PREFIX}{partner_batch_id}/"

    # transactions.csv first. Always written, even if empty (header
    # only) - a consistent output shape is easier to build downstream
    # tooling against than "sometimes this file exists."
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=FIELDNAMES, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(valid_rows)
    s3_client.put_object(
        Bucket=bucket,
        Key=f"{prefix}transactions.csv",
        Body=output.getvalue().encode("utf-8"),
    )

    # summary.json last - its presence/freshness is the "this batch
    # finished processing" signal _is_reprocessing() checks for.
    exceptions_detail = [
        {
            "transaction_id": r.transaction_id,
            "violations": [
                {"rule": v.rule, "severity": v.severity, "detail": v.detail}
                for v in r.violations
            ],
        }
        for r in exception_rows
    ]
    summary_doc = {
        "partner_batch_id": partner_batch_id,
        "source_key": source_key,
        "processed_at": datetime.now(timezone.utc).isoformat(),
        "counts": summary,
        "exceptions": exceptions_detail,
    }
    s3_client.put_object(
        Bucket=bucket,
        Key=f"{prefix}summary.json",
        Body=json.dumps(summary_doc, indent=2).encode("utf-8"),
    )
    logger.info(
        "Wrote output for batch %s: %d valid rows, %d exception entries",
        partner_batch_id, len(valid_rows), len(exceptions_detail),
    )


def handler(event, context):
    logger.info("Received S3 event: %s", json.dumps(event))
    records = event.get("Records", [])
    logger.info("Event contains %d record(s)", len(records))

    for record in records:
        bucket = record.get("s3", {}).get("bucket", {}).get("name")
        s3_object = record.get("s3", {}).get("object", {})
        key = s3_object.get("key")
        size_bytes = s3_object.get("size", 0)
        logger.info("Object created: bucket=%s key=%s size=%d", bucket, key, size_bytes)

        parsed = _parse_filename(key)
        if parsed is None:
            logger.warning("Filename does not match expected convention: %s", key)
            _send_rejection_alert(bucket, key, "filename does not match the expected convention")
            continue

        if size_bytes > MAX_FILE_SIZE_BYTES:
            logger.warning("File exceeds max size: %d bytes (limit %d): %s",
                            size_bytes, MAX_FILE_SIZE_BYTES, key)
            _send_rejection_alert(
                bucket, key,
                f"file size ({size_bytes} bytes) exceeds the {MAX_FILE_SIZE_BYTES}-byte limit for a daily file",
            )
            continue

        date_str, partner_batch_id = parsed

        staleness_warning = _check_staleness(date_str)
        if staleness_warning:
            logger.warning("%s (key=%s)", staleness_warning, key)

        if _is_reprocessing(bucket, partner_batch_id):
            logger.warning(
                "Reprocessing detected for batch %s - previous result will be overwritten (key=%s)",
                partner_batch_id, key,
            )
            _send_reprocessing_alert(bucket, key, partner_batch_id)

        try:
            rows = _read_csv_rows(bucket, key)
        except UnicodeDecodeError:
            logger.warning("File failed to decode as UTF-8: %s", key)
            _send_rejection_alert(bucket, key, "file could not be decoded as UTF-8")
            continue

        valid_rows, exception_rows, summary = validate_batch(rows)
        logger.info("Validation summary for %s: %s", key, summary)

        _write_output(bucket, partner_batch_id, valid_rows, exception_rows, summary, key)

    return {
        "statusCode": 200,
        "body": json.dumps({"message": "Handler completed"}),
    }
