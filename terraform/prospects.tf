#==============================================================================
# Prospects — configurable job-feed ingestion (surface-only, never auto-apply).
# Fetches var.feed_url on a schedule into S3; the dashboard shows a review queue.
# No-ops until you set feed_url to a source you're allowed to pull (respect ToS).
#==============================================================================

data "archive_file" "prospects" {
  type        = "zip"
  source_dir  = "${path.module}/../src/prospects"
  output_path = "${path.module}/build/prospects.zip"
}

resource "aws_iam_role" "prospects" {
  name               = "${local.name}-prospects-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "prospects_basic" {
  role       = aws_iam_role.prospects.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "prospects" {
  statement {
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${aws_s3_bucket.docs.arn}/prospects/*"]
  }
}

resource "aws_iam_role_policy" "prospects" {
  name   = "${local.name}-prospects-policy"
  role   = aws_iam_role.prospects.id
  policy = data.aws_iam_policy_document.prospects.json
}

resource "aws_lambda_function" "prospects" {
  function_name    = "${local.name}-prospects"
  role             = aws_iam_role.prospects.arn
  runtime          = "python3.12"
  handler          = "lambda_function.handler"
  filename         = data.archive_file.prospects.output_path
  source_code_hash = data.archive_file.prospects.output_base64sha256
  timeout          = 60
  environment {
    variables = {
      DOCS_BUCKET = aws_s3_bucket.docs.bucket
      FEED_URL    = var.feed_url
    }
  }
  tags = local.tags
}

resource "aws_cloudwatch_event_rule" "prospects" {
  name                = "${local.name}-prospects"
  schedule_expression = "rate(12 hours)"
  tags                = local.tags
}

resource "aws_cloudwatch_event_target" "prospects" {
  rule = aws_cloudwatch_event_rule.prospects.name
  arn  = aws_lambda_function.prospects.arn
}

resource "aws_lambda_permission" "prospects" {
  statement_id  = "AllowEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.prospects.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.prospects.arn
}
