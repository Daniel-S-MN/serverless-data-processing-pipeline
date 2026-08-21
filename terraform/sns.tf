# terraform/sns.tf
#
# When the Lambda rejects an incoming file (filename doesn't match
# the expected convention), it publishes to this topic instead of
# touching the file. A human then handles the file manually via the
# AWS Console — this is a deliberate scope choice: automated
# DETECTION, human-driven REMEDIATION.

resource "aws_sns_topic" "rejected_files" {
  name = "${var.project_name}-${var.environment}-rejected-files"
}

# Email subscriptions require manual, one-time confirmation - AWS
# emails a confirmation link to this address after 'apply', and
# delivery doesn't start until that link is clicked. This can't be
# automated by Terraform (or anything else) by design, since it's
# meant to prevent subscribing an address you don't own.
resource "aws_sns_topic_subscription" "rejected_files_email" {
  topic_arn = aws_sns_topic.rejected_files.arn
  protocol  = "email"
  endpoint  = var.notification_email
}
