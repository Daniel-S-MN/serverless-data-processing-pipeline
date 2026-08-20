# terraform/variables.tf

variable "project_name" {
  description = "Short project identifier used in resource names and tags."
  type        = string
  default     = "transaction-pipeline"
}

variable "aws_region" {
  description = "AWS region this project's resources are created in."
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Deployment environment name, used in resource naming/tags."
  type        = string
  default     = "dev"
}

# --- S3 (application data bucket) ---

variable "incoming_prefix" {
  description = "S3 key prefix a partner's daily CSV must land under to trigger processing."
  type        = string
  default     = "incoming/"
}

variable "processed_prefix" {
  description = "S3 key prefix successfully processed output is written to."
  type        = string
  default     = "processed/"
}

variable "quarantine_prefix" {
  description = "S3 key prefix for files rejected before processing (e.g. bad filename convention)."
  type        = string
  default     = "quarantine/"
}

# --- Lambda ---

variable "lambda_timeout_seconds" {
  description = "Lambda function timeout, in seconds."
  type        = number
  default     = 60
}

variable "lambda_memory_mb" {
  description = "Lambda function memory allocation, in MB."
  type        = number
  default     = 256
}
