terraform {
  required_version = ">= 1.9"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
    # Used by data.archive_file to zip each Lambda. It was in use but absent from
    # required_providers, so `terraform init` was free to resolve any major
    # version of it — caught by tflint (terraform_required_providers).
    archive = { source = "hashicorp/archive", version = "~> 2.7" }
  }
}

provider "aws" {
  region = var.region
}

data "aws_caller_identity" "me" {}

locals {
  name = var.name_prefix
  acct = data.aws_caller_identity.me.account_id
  tags = {
    Project   = "job-hunt-command-center"
    ManagedBy = "terraform"
  }
}
