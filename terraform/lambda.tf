# terraform/lambda.tf
#
# Placeholder Lambda for now — src/lambda/handler.py just logs the
# event it received. This phase is about proving the S3 -> Lambda
# trigger works end-to-end; the real filename-validation and
# transformation logic replaces this handler in a later phase.

# Zips the handler source into a deployable package. Using
# archive_file (a Terraform built-in data source, no extra provider
# needed) instead of a manual zip step keeps packaging part of
# `terraform apply` rather than a separate script to remember to run.
data "archive_file" "lambda_package" {
  type        = "zip"
  source_dir  = "${path.module}/../src/lambda"
  output_path = "${path.module}/build/lambda_package.zip"
}

# --- Execution role: what the Lambda is allowed to DO once running ---
# (separate from aws_lambda_permission below, which controls who is
# allowed to INVOKE it — a distinction worth keeping straight.)

resource "aws_iam_role" "lambda_exec" {
  name = "${var.project_name}-${var.environment}-lambda-exec"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })
}

# Baseline CloudWatch Logs permissions every Lambda needs, via AWS's
# own managed policy rather than hand-writing the equivalent JSON.
resource "aws_iam_role_policy_attachment" "lambda_basic_logging" {
  role       = aws_iam_role.lambda_exec.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# Least-privilege S3 access: read from incoming/, write to
# processed/ and quarantine/. No wildcard "*" bucket access, and no
# delete permission — this Lambda never needs to delete objects.
resource "aws_iam_role_policy" "lambda_s3_access" {
  name = "${var.project_name}-${var.environment}-lambda-s3-access"
  role = aws_iam_role.lambda_exec.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "${aws_s3_bucket.data.arn}/${var.incoming_prefix}*"
      },
      {
        Effect = "Allow"
        Action = ["s3:PutObject"]
        Resource = [
          "${aws_s3_bucket.data.arn}/${var.processed_prefix}*",
          "${aws_s3_bucket.data.arn}/${var.quarantine_prefix}*",
        ]
      }
    ]
  })
}

# Permission to publish rejected-file alerts to the SNS topic
# defined in sns.tf. Scoped to that one specific topic ARN, not
# wildcarded to all SNS topics in the account.
resource "aws_iam_role_policy" "lambda_sns_publish" {
  name = "${var.project_name}-${var.environment}-lambda-sns-publish"
  role = aws_iam_role.lambda_exec.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["sns:Publish"]
        Resource = aws_sns_topic.rejected_files.arn
      }
    ]
  })
}

resource "aws_lambda_function" "processor" {
  function_name = "${var.project_name}-${var.environment}-processor"
  role          = aws_iam_role.lambda_exec.arn
  handler       = "handler.handler"
  runtime       = "python3.12"
  timeout       = var.lambda_timeout_seconds
  memory_size   = var.lambda_memory_mb

  filename         = data.archive_file.lambda_package.output_path
  source_code_hash = data.archive_file.lambda_package.output_base64sha256

  environment {
    variables = {
      PROCESSED_PREFIX  = var.processed_prefix
      QUARANTINE_PREFIX = var.quarantine_prefix
      SNS_TOPIC_ARN     = aws_sns_topic.rejected_files.arn
    }
  }
}

# --- Invoke permission: who is allowed to INVOKE the Lambda ---
# This is the resource-based policy people most often forget. Without
# it, the S3 event notification in s3.tf will silently fail to
# trigger anything — no error, it just never fires.
resource "aws_lambda_permission" "allow_s3_invoke" {
  statement_id  = "AllowS3Invoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.processor.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.data.arn
}
