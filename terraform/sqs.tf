# terraform/sqs.tf
#
# This is NOT the classic "Lambda polls SQS" pattern - S3 invokes
# this Lambda directly and asynchronously (see s3.tf), so there's no
# queue in front of it to poll. Instead, this queue is configured as
# the Lambda's ASYNC INVOCATION FAILURE DESTINATION - a distinct AWS
# concept from SQS's own redrive-policy DLQs.
#
# AWS automatically retries a failed async invocation twice. Only if
# BOTH retries also fail does the event land here - meaning this
# queue represents genuine, unhandled Lambda failures (an actual
# exception), not the expected/handled cases (bad filename, oversized
# file, decode failure) that the handler already catches gracefully
# and alerts about via SNS instead. See README for the fuller
# SNS-vs-SQS distinction.

resource "aws_sqs_queue" "processing_failures" {
  name = "${var.project_name}-${var.environment}-processing-failures"

  # 14 days (SQS's max) rather than the 4-day default - a failure
  # that isn't investigated over a long weekend or a vacation
  # shouldn't quietly expire before anyone's looked at it.
  message_retention_seconds = 1209600
}

# The Lambda's own execution role needs permission to WRITE to this
# queue - async invocation failure destinations are delivered using
# the function's own execution role, not a separate AWS-managed
# mechanism.
resource "aws_iam_role_policy" "lambda_sqs_dlq_send" {
  name = "${var.project_name}-${var.environment}-lambda-sqs-dlq-send"
  role = aws_iam_role.lambda_exec.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = aws_sqs_queue.processing_failures.arn
      }
    ]
  })
}

# Configures the Lambda's asynchronous invocation behavior. This is
# what actually wires the queue in - separate from the function
# resource itself, since it's specifically about async invoke
# behavior (retries + destinations), not the function's core config.
resource "aws_lambda_function_event_invoke_config" "processor_async" {
  function_name = aws_lambda_function.processor.function_name

  maximum_retry_attempts = 2 # AWS's own default, stated explicitly rather than implied

  destination_config {
    on_failure {
      destination = aws_sqs_queue.processing_failures.arn
    }
  }
}
