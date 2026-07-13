# --- Secrets Manager: email (IMAP App Password) credential --------------------
# Placeholder until the one-time credential is added out-of-band. The scan Lambda
# treats a "REPLACE…" password as unconfigured and no-ops.

resource "aws_secretsmanager_secret" "email" {
  name        = "${local.name}/email-credentials"
  description = "Google App Password (IMAP) for the inbox-scan Lambda"
  tags        = local.tags
}

resource "aws_secretsmanager_secret_version" "email" {
  secret_id = aws_secretsmanager_secret.email.id
  secret_string = jsonencode({
    email        = var.owner_email
    app_password = "REPLACE_WITH_GOOGLE_APP_PASSWORD"
    imap_host    = "imap.gmail.com"
  })
  lifecycle { ignore_changes = [secret_string] } # don't clobber the real value later
}

# --- inbox-scan Lambda --------------------------------------------------------

data "archive_file" "inbox_scan" {
  type        = "zip"
  source_dir  = "${path.module}/../src/inbox_scan"
  output_path = "${path.module}/build/inbox_scan.zip"
}

resource "aws_iam_role" "inbox_scan" {
  name               = "${local.name}-inbox-scan-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "inbox_scan_basic" {
  role       = aws_iam_role.inbox_scan.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "inbox_scan" {
  statement {
    # read apps to match, and update them to auto-advance status from email
    actions   = ["dynamodb:Scan", "dynamodb:GetItem", "dynamodb:PutItem"]
    resources = [aws_dynamodb_table.applications.arn]
  }
  statement {
    # write findings + scan existing ids for idempotency
    actions   = ["dynamodb:PutItem", "dynamodb:Scan"]
    resources = [aws_dynamodb_table.email_events.arn]
  }
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.email.arn]
  }
}

resource "aws_iam_role_policy" "inbox_scan" {
  name   = "${local.name}-inbox-scan-policy"
  role   = aws_iam_role.inbox_scan.id
  policy = data.aws_iam_policy_document.inbox_scan.json
}

resource "aws_lambda_function" "inbox_scan" {
  function_name    = "${local.name}-inbox-scan"
  role             = aws_iam_role.inbox_scan.arn
  runtime          = "python3.12"
  handler          = "lambda_function.handler"
  filename         = data.archive_file.inbox_scan.output_path
  source_code_hash = data.archive_file.inbox_scan.output_base64sha256
  timeout          = 120
  environment {
    variables = {
      APPS_TABLE   = aws_dynamodb_table.applications.name
      EVENTS_TABLE = aws_dynamodb_table.email_events.name
      SECRET_ID    = aws_secretsmanager_secret.email.arn
    }
  }
  tags = local.tags
}

resource "aws_cloudwatch_event_rule" "inbox_scan" {
  name                = "${local.name}-inbox-scan"
  schedule_expression = "rate(6 hours)"
  tags                = local.tags
}

resource "aws_cloudwatch_event_target" "inbox_scan" {
  rule = aws_cloudwatch_event_rule.inbox_scan.name
  arn  = aws_lambda_function.inbox_scan.arn
}

resource "aws_lambda_permission" "inbox_scan" {
  statement_id  = "AllowEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.inbox_scan.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.inbox_scan.arn
}

# --- nudge Lambda -------------------------------------------------------------

data "archive_file" "nudge" {
  type        = "zip"
  source_dir  = "${path.module}/../src/nudge"
  output_path = "${path.module}/build/nudge.zip"
}

resource "aws_iam_role" "nudge" {
  name               = "${local.name}-nudge-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "nudge_basic" {
  role       = aws_iam_role.nudge.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "nudge" {
  statement {
    actions   = ["dynamodb:Scan"]
    resources = [aws_dynamodb_table.applications.arn]
  }
  statement {
    actions   = ["ses:SendEmail"]
    resources = ["arn:aws:ses:${var.region}:${local.acct}:identity/abheenash.com"]
  }
}

resource "aws_iam_role_policy" "nudge" {
  name   = "${local.name}-nudge-policy"
  role   = aws_iam_role.nudge.id
  policy = data.aws_iam_policy_document.nudge.json
}

resource "aws_lambda_function" "nudge" {
  function_name    = "${local.name}-nudge"
  role             = aws_iam_role.nudge.arn
  runtime          = "python3.12"
  handler          = "lambda_function.handler"
  filename         = data.archive_file.nudge.output_path
  source_code_hash = data.archive_file.nudge.output_base64sha256
  timeout          = 30
  environment {
    variables = {
      APPS_TABLE  = aws_dynamodb_table.applications.name
      SES_SENDER  = "no-reply@abheenash.com"
      OWNER_EMAIL = var.owner_email
    }
  }
  tags = local.tags
}

resource "aws_cloudwatch_event_rule" "nudge" {
  name                = "${local.name}-nudge"
  schedule_expression = "cron(0 14 * * ? *)" # 14:00 UTC daily
  tags                = local.tags
}

resource "aws_cloudwatch_event_target" "nudge" {
  rule = aws_cloudwatch_event_rule.nudge.name
  arn  = aws_lambda_function.nudge.arn
}

resource "aws_lambda_permission" "nudge" {
  statement_id  = "AllowEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.nudge.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.nudge.arn
}
