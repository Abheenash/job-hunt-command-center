# --- Openings Radar -----------------------------------------------------------
# A daily scanner Lambda pulls entry-level cloud/DevOps/SRE/support roles from
# target companies' documented ATS JSON APIs (Greenhouse/Ashby/Workday/amazon.jobs),
# flags visa sponsorship, scores fit with a FREE deterministic keyword+seniority
# signal (no AI / no per-scan cost), dedups by company|title, and stores the matches.
# The API reads this table (GET /openings) and can trigger an on-demand rescan.

resource "aws_dynamodb_table" "openings" {
  name         = "${local.name}-openings"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "openingId"

  attribute {
    name = "openingId"
    type = "S"
  }

  ttl {
    attribute_name = "expireAt"
    enabled        = true
  }

  point_in_time_recovery { enabled = true }
  tags = local.tags
}

# Purge-proof suppression list: company|title signatures the user dismissed / tracked /
# already applied to. Lives in its OWN table so purging the openings table (which we do
# after scoring/source changes) can never resurrect a rejected posting.
resource "aws_dynamodb_table" "openings_suppress" {
  name         = "${local.name}-openings-suppress"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "sig"

  attribute {
    name = "sig"
    type = "S"
  }

  ttl {
    attribute_name = "expireAt"
    enabled        = true
  }

  tags = local.tags
}

data "archive_file" "openings_scan" {
  type        = "zip"
  source_dir  = "${path.module}/../src/openings"
  output_path = "${path.module}/build/openings_scan.zip"
}

resource "aws_iam_role" "openings_scan" {
  name               = "${local.name}-openings-scan-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "openings_scan_basic" {
  role       = aws_iam_role.openings_scan.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "openings_scan" {
  statement {
    actions   = ["dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:Scan"]
    resources = [aws_dynamodb_table.openings.arn]
  }
  statement {
    sid       = "ApplicationsRead" # skip roles already in the tracker
    actions   = ["dynamodb:Scan"]
    resources = [aws_dynamodb_table.applications.arn]
  }
  statement {
    sid       = "SuppressRead" # skip dismissed / tracked signatures (purge-proof)
    actions   = ["dynamodb:Scan"]
    resources = [aws_dynamodb_table.openings_suppress.arn]
  }
}

resource "aws_iam_role_policy" "openings_scan" {
  name   = "${local.name}-openings-scan-policy"
  role   = aws_iam_role.openings_scan.id
  policy = data.aws_iam_policy_document.openings_scan.json
}

resource "aws_lambda_function" "openings_scan" {
  function_name    = "${local.name}-openings-scan"
  role             = aws_iam_role.openings_scan.arn
  runtime          = "python3.12"
  handler          = "lambda_function.handler"
  filename         = data.archive_file.openings_scan.output_path
  source_code_hash = data.archive_file.openings_scan.output_base64sha256
  timeout          = 600  # ~60 ATS fetches (large boards) + dedup/scoring (all deterministic)
  memory_size      = 1024 # more CPU = faster fetch/parse of many large boards
  tags             = local.tags
  environment {
    variables = {
      OPENINGS_TABLE = aws_dynamodb_table.openings.name
      APPS_TABLE     = aws_dynamodb_table.applications.name
      SUPPRESS_TABLE = aws_dynamodb_table.openings_suppress.name
      # Adzuna aggregator is opt-in — set these to a free key from developer.adzuna.com
      ADZUNA_APP_ID  = var.adzuna_app_id
      ADZUNA_APP_KEY = var.adzuna_app_key
    }
  }
}

resource "aws_cloudwatch_event_rule" "openings_scan" {
  name                = "${local.name}-openings-scan"
  schedule_expression = "cron(0 13 * * ? *)" # once daily at 13:00 UTC (cost control)
  tags                = local.tags
}

resource "aws_cloudwatch_event_target" "openings_scan" {
  rule = aws_cloudwatch_event_rule.openings_scan.name
  arn  = aws_lambda_function.openings_scan.arn
}

resource "aws_lambda_permission" "openings_scan_events" {
  statement_id  = "AllowEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.openings_scan.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.openings_scan.arn
}
