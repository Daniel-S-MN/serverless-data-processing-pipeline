# terraform/lambda.tf
#
# Lambda handler performs filename validation, staleness/reprocessing
# checks, and (next phase) real transformation/validation logic.

# Zips the handler source into a deployable package. Pulls files
# individually from two different directories (src/lambda AND the
# shared src/schema.py) rather than one source_dir, so schema.py
# stays a single source of truth used by both the dataset generator
# and the Lambda — no duplicate copy maintained on disk.
data "archive_file" "lambda_package" {
  type        = "zip"
  output_path = "${path.module}/build/lambda_package.zip"

  source {
    content  = file("${path.module}/../src/lambda/handler.py")
    filename = "handler.py"
  }

  source {
    content  = file("${path.module}/../src/lambda/validation.py")
    filename = "validation.py"
  }

  source {
    content  = file("${path.module}/../src/schema.py")
    filename = "schema.py"
  }
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
      },
      {
        # ListBucket is a BUCKET-level permission (unlike GetObject/
        # PutObject above, which are object-level) - it's what
        # list_objects_v2 needs to check whether a given batch has
        # already been processed, before writing new output. The
        # Condition restricts it to only listing under processed/,
        # not the whole bucket.
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = aws_s3_bucket.data.arn
        Condition = {
          StringLike = {
            "s3:prefix" = "${var.processed_prefix}*"
          }
        }
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
      MAX_FILE_SIZE_MB  = var.max_file_size_mb
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
