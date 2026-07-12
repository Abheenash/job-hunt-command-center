# --- DynamoDB: the rich application record + the classified email events -------

resource "aws_dynamodb_table" "applications" {
  name         = "${local.name}-applications"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "appId"

  attribute {
    name = "appId"
    type = "S"
  }

  point_in_time_recovery { enabled = true }
  # DynamoDB encrypts at rest by default (AWS-owned key) — no explicit block
  # needed; the aws/dynamodb managed-key path hit a create-race on first use.
  tags = local.tags
}

resource "aws_dynamodb_table" "email_events" {
  name         = "${local.name}-email-events"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "eventId"

  attribute {
    name = "eventId"
    type = "S"
  }
  attribute {
    name = "appId"
    type = "S"
  }

  # Query all classified emails linked to one application.
  global_secondary_index {
    name            = "byApp"
    hash_key        = "appId"
    projection_type = "ALL"
  }

  point_in_time_recovery { enabled = true }
  # DynamoDB encrypts at rest by default (AWS-owned key) — no explicit block
  # needed; the aws/dynamodb managed-key path hit a create-race on first use.
  tags = local.tags
}

# --- S3: private, versioned document store (immutable as-sent snapshots) -------

resource "aws_s3_bucket" "docs" {
  bucket = "${local.name}-docs-${local.acct}"
  tags   = local.tags
}

resource "aws_s3_bucket_public_access_block" "docs" {
  bucket                  = aws_s3_bucket.docs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "docs" {
  bucket = aws_s3_bucket.docs.id
  versioning_configuration { status = "Enabled" }
}

# SSE-S3 (AES256) keeps the project free; upgrade to aws:kms with a CMK if desired.
resource "aws_s3_bucket_server_side_encryption_configuration" "docs" {
  bucket = aws_s3_bucket.docs.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
    bucket_key_enabled = true
  }
}

# CORS so the browser can PUT/GET documents directly via presigned URLs.
resource "aws_s3_bucket_cors_configuration" "docs" {
  bucket = aws_s3_bucket.docs.id
  cors_rule {
    allowed_methods = ["PUT", "GET"]
    allowed_origins = ["https://${aws_cloudfront_distribution.site.domain_name}", "http://localhost:*"]
    allowed_headers = ["*"]
    expose_headers  = ["ETag"]
    max_age_seconds = 3000
  }
}
