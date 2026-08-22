"""
Lambda handler for the S3-triggered transaction pipeline.

Current behavior, per incoming object:
  1. Validate the filename against the expected convention:
       transactions_{YYYY-MM-DD}_{partner_batch_id}.csv
     No match -> publish a rejection alert to SNS, leave the file in
     place, stop. Automated DETECTION, human-driven REMEDIATION.
  2. If the filename is valid, check the embedded date for staleness
     (more than a few days old, or in the future). This is a WARNING
     only - it does not block processing. Late/backfilled files are
     a legitimate case, not necessarily a problem.
  3. Check whether processed/{partner_batch_id}/ already has output.
     If so, this is a reprocessing event (e.g. a corrected afternoon
     resubmission of a morning file) - log a warning and publish an
     alert, but still allow it to proceed. The eventual write step
     overwrites the prior result; nothing is silently clobbered
     without a record that it happened.
  4. Log that the file would proceed to real processing. (Real
     transformation/validation logic replaces this in a later phase.)
"""

import json
import logging
import os
import re
from datetime import date, datetime

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3_client = boto3.client("s3")
sns_client = boto3.client("sns")

SNS_TOPIC_ARN = os.environ.get("SNS_TOPIC_ARN")
PROCESSED_PREFIX = os.environ.get("PROCESSED_PREFIX", "processed/")

# transactions_2026-08-20_BATCH-001.csv -> ("2026-08-20", "BATCH-001")
FILENAME_PATTERN = re.compile(
    r"^transactions_(\d{4}-\d{2}-\d{2})_([A-Za-z0-9\-]+)\.csv$"
)

# How many days old (or in the future) a filename's date can be
# before it's flagged as an anomaly worth a human glancing at. Not a
# hard rule - just wide enough to comfortably cover a weekend or
# holiday delay without flagging every routine late file.
STALENESS_THRESHOLD_DAYS = 3


def _parse_filename(key: str):
    """
    Returns (date_str, partner_batch_id) if the filename matches the
    expected convention, otherwise None.
    """
    filename = key.split("/")[-1]
    match = FILENAME_PATTERN.match(filename)
    if not match:
        return None
    return match.group(1), match.group(2)


def _check_staleness(date_str: str) -> str | None:
    """
    Returns a warning message if the filename's date is more than
    STALENESS_THRESHOLD_DAYS away from today (past or future),
    otherwise None. Never blocks processing - staleness is a signal
    to look at, not a rejection reason.
    """
    filename_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    delta_days = (date.today() - filename_date).days

    if delta_days > STALENESS_THRESHOLD_DAYS:
        return f"Filename date {date_str} is {delta_days} days in the past"
    if delta_days < -STALENESS_THRESHOLD_DAYS:
        return f"Filename date {date_str} is {abs(delta_days)} days in the future"
    return None


def _is_reprocessing(bucket: str, partner_batch_id: str) -> bool:
    """
    Checks whether processed/{partner_batch_id}/ already has any
    objects. MaxKeys=1 keeps this a cheap existence check rather than
    a full listing - we only need to know if anything is there.
    """
    prefix = f"{PROCESSED_PREFIX}{partner_batch_id}/"
    response = s3_client.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=1)
    return response.get("KeyCount", 0) > 0


def _publish_alert(subject: str, message: str, bucket: str, key: str) -> None:
    if not SNS_TOPIC_ARN:
        logger.error(
            "SNS_TOPIC_ARN not set - cannot send alert for %s/%s", bucket, key
        )
        return
    sns_client.publish(TopicArn=SNS_TOPIC_ARN, Subject=subject, Message=message)
    logger.info("Alert published (%s) for bucket=%s key=%s", subject, bucket, key)


def _send_rejection_alert(bucket: str, key: str) -> None:
    message = (
        f"A file was rejected because its name doesn't match the expected "
        f"convention (transactions_YYYY-MM-DD_partner-batch-id.csv).\n\n"
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


def handler(event, context):
    logger.info("Received S3 event: %s", json.dumps(event))

    records = event.get("Records", [])
    logger.info("Event contains %d record(s)", len(records))

    for record in records:
        bucket = record.get("s3", {}).get("bucket", {}).get("name")
        key = record.get("s3", {}).get("object", {}).get("key")
        logger.info("Object created: bucket=%s key=%s", bucket, key)

        parsed = _parse_filename(key)
        if parsed is None:
            logger.warning("Filename does not match expected convention: %s", key)
            _send_rejection_alert(bucket, key)
            continue

        date_str, partner_batch_id = parsed

        staleness_warning = _check_staleness(date_str)
        if staleness_warning:
            logger.warning("%s (key=%s)", staleness_warning, key)

        if _is_reprocessing(bucket, partner_batch_id):
            logger.warning(
                "Reprocessing detected for batch %s - previous result will be "
                "overwritten (key=%s)",
                partner_batch_id,
                key,
            )
            _send_reprocessing_alert(bucket, key, partner_batch_id)

        # Placeholder for now - real transformation/validation logic
        # replaces this line in a later phase.
        logger.info(
            "Filename valid - would proceed to processing: %s (batch=%s)",
            key,
            partner_batch_id,
        )

    return {
        "statusCode": 200,
        "body": json.dumps({"message": "Handler completed"}),
    }
