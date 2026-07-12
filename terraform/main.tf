terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.60" }
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
