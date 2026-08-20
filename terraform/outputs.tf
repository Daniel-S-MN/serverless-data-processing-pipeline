# terraform/outputs.tf

output "data_bucket_name" {
  description = "Name of the S3 bucket holding incoming/processed/quarantine transaction files."
  value       = aws_s3_bucket.data.id
}

output "lambda_function_name" {
  description = "Name of the processor Lambda function."
  value       = aws_lambda_function.processor.function_name
}

output "lambda_function_arn" {
  description = "ARN of the processor Lambda function."
  value       = aws_lambda_function.processor.arn
}
