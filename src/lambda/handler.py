"""
Lambda handler for the S3-triggered transaction pipeline.

Current behavior:
  1. Parse the S3 object key from the triggering event.
  2. Validate the filename against the expected convention:
       transactions_{YYYY-MM-DD}_{partner_batch_id}.csv
  3. If it matches: log that the file WOULD proceed to processing.
     (Real transformation/validation logic replaces this in a later
     phase - this handler doesn't touch the file yet either way.)
  4. If it doesn't match: publish an alert to the SNS topic and stop.
     The file is deliberately left untouched in incoming/ - this is
     automated DETECTION, human-driven REMEDIATION. A person reviews
     the alert and handles the file manually via the AWS Console.
"""

import json
import logging
import os
import re

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

sns_client = boto3.client("sns")

SNS_TOPIC_ARN = os.environ.get("SNS_TOPIC_ARN")

# transactions_2026-08-20_BATCH-001.csv
FILENAME_PATTERN = re.compile(
    r"^transactions_\d{4}-\d{2}-\d{2}_[A-Za-z0-9\-]+\.csv$"
)


def _is_valid_filename(key: str) -> bool:
    """
    Validate against the naming convention only. Checking the
    incoming/ prefix itself is redundant here - the S3 event
    notification is already filtered to that prefix, so any key this
    handler receives is guaranteed to start with it.
    """
    filename = key.split("/")[-1]
    return bool(FILENAME_PATTERN.match(filename))


def _send_rejection_alert(bucket: str, key: str) -> None:
    if not SNS_TOPIC_ARN:
        logger.error(
            "SNS_TOPIC_ARN not set - cannot send rejection alert for %s/%s",
            bucket,
            key,
        )
        return

    subject = "Transaction Pipeline: Rejected File"
    message = (
        f"A file was rejected because its name doesn't match the expected "
        f"convention (transactions_YYYY-MM-DD_partner-batch-id.csv).\n\n"
        f"Bucket: {bucket}\n"
        f"Key: {key}\n\n"
        f"The file has been left in place. Review it manually in the AWS "
        f"Console and rename, move, or delete it as appropriate."
    )

    sns_client.publish(TopicArn=SNS_TOPIC_ARN, Subject=subject, Message=message)
    logger.info("Rejection alert published for bucket=%s key=%s", bucket, key)


def handler(event, context):
    logger.info("Received S3 event: %s", json.dumps(event))

    records = event.get("Records", [])
    logger.info("Event contains %d record(s)", len(records))

    for record in records:
        bucket = record.get("s3", {}).get("bucket", {}).get("name")
        key = record.get("s3", {}).get("object", {}).get("key")
        logger.info("Object created: bucket=%s key=%s", bucket, key)

        if _is_valid_filename(key):
            # Placeholder for now - real transformation/validation
            # logic replaces this line in a later phase.
            logger.info("Filename valid - would proceed to processing: %s", key)
        else:
            logger.warning("Filename does not match expected convention: %s", key)
            _send_rejection_alert(bucket, key)

    return {
        "statusCode": 200,
        "body": json.dumps({"message": "Handler completed"}),
    }
