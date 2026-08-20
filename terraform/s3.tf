# terraform/s3.tf
#
# The APPLICATION data bucket — not to be confused with the separate
# state bucket referenced in main.tf's backend block. This bucket
# holds actual transaction CSVs, organized under three key prefixes
# (S3 has no real folders — these are just prefixes on object keys):
#
#   incoming/    partner drops daily CSVs here — this is what triggers Lambda
#   processed/   successfully validated/transformed output
#   quarantine/  files rejected before processing even begins
#                (e.g. filename doesn't match the expected convention)
#
# random_id below is unrelated to (and duplicated from) the one in
# portfolio-shared-infra — S3 bucket names are globally unique across
# ALL of AWS, so every bucket this project creates needs its own
# collision-avoidance suffix.

resource "random_id" "data_bucket_suffix" {
  byte_length = 4
}

resource "aws_s3_bucket" "data" {
  bucket = "${var.project_name}-${var.environment}-data-${random_id.data_bucket_suffix.hex}"
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  bucket = aws_s3_bucket.data.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket = aws_s3_bucket.data.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Expire processed output after 90 days. Not required by the brief,
# but a small, easy detail that shows awareness of storage cost /
# data lifecycle management — worth mentioning in the README.
resource "aws_s3_bucket_lifecycle_configuration" "data" {
  bucket = aws_s3_bucket.data.id

  rule {
    id     = "expire-processed-output"
    status = "Enabled"

    filter {
      prefix = var.processed_prefix
    }

    expiration {
      days = 90
    }
  }
}

# The actual trigger: any .csv landing under incoming/ invokes the
# Lambda. Scoped by BOTH prefix and suffix so this never fires on
# writes to processed/ or quarantine/, or on non-CSV files someone
# drops in incoming/ by mistake.
resource "aws_s3_bucket_notification" "incoming_csv_trigger" {
  bucket = aws_s3_bucket.data.id

  lambda_function {
    lambda_function_arn = aws_lambda_function.processor.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = var.incoming_prefix
    filter_suffix       = ".csv"
  }

  # Terraform needs this explicit dependency because the permission
  # granting S3 the right to invoke Lambda (in lambda.tf) must exist
  # BEFORE this notification is created, or AWS rejects the
  # notification config outright.
  depends_on = [aws_lambda_permission.allow_s3_invoke]
}
