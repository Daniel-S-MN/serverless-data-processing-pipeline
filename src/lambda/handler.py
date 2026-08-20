"""
Placeholder Lambda handler.

This exists to prove the S3 -> Lambda trigger is wired correctly
before any real filename-validation or transaction-processing logic
is added. It does exactly one thing: logs what S3 event it received.

Once the trigger is confirmed working end-to-end, this gets replaced
with the real handler (filename validation -> quarantine routing ->
transformation/validation pipeline).
"""

import json
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handler(event, context):
    logger.info("Received S3 event: %s", json.dumps(event))

    records = event.get("Records", [])
    logger.info("Event contains %d record(s)", len(records))

    for record in records:
        bucket = record.get("s3", {}).get("bucket", {}).get("name")
        key = record.get("s3", {}).get("object", {}).get("key")
        logger.info("Object created: bucket=%s key=%s", bucket, key)

    return {
        "statusCode": 200,
        "body": json.dumps({"message": "Placeholder handler invoked successfully"}),
    }
