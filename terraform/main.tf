# terraform/main.tf
#
# This project's state lives in the SHARED backend created by the
# separate portfolio-shared-infra repo (see its README). Unlike that
# bootstrap config, this one uses REMOTE state — the whole point of
# the shared bucket/table existing is so every project (this one,
# and the three still to come) can point at it.
#
# The bucket/table names below come from portfolio-shared-infra's
# `terraform apply` output. Only `key` differs between projects —
# it's this project's unique path inside the shared bucket.

terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }

  backend "s3" {
    bucket         = "daniel-portfolio-tfstate-dbcbeaf8"
    key            = "transaction-pipeline/terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "daniel-portfolio-tfstate-locks"
    encrypt        = true
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = var.project_name
      ManagedBy = "terraform"
    }
  }
}
