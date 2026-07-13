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

# --- inbox pipeline -----------------------------------------------------------
# The old monolithic inbox-scan Lambda was decomposed into an event-driven
# pipeline (EventBridge -> Scanner -> SQS(+DLQ) -> Dispatcher -> Step Functions
# Express: Classify -> Enrich). See pipeline.tf.

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
    # In the SES sandbox, authorization is checked against BOTH the sender domain
    # identity and the (verified) recipient identity — so grant both.
    actions = ["ses:SendEmail"]
    resources = [
      "arn:aws:ses:${var.region}:${local.acct}:identity/abheenash.com",
      "arn:aws:ses:${var.region}:${local.acct}:identity/${var.owner_email}",
    ]
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

# --- weekly digest Lambda -----------------------------------------------------

data "archive_file" "digest" {
  type        = "zip"
  source_dir  = "${path.module}/../src/digest"
  output_path = "${path.module}/build/digest.zip"
}

resource "aws_iam_role" "digest" {
  name               = "${local.name}-digest-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "digest_basic" {
  role       = aws_iam_role.digest.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "digest" {
  statement {
    actions   = ["dynamodb:Scan"]
    resources = [aws_dynamodb_table.applications.arn]
  }
  statement {
    # In the SES sandbox, authorization is checked against BOTH the sender domain
    # identity and the (verified) recipient identity — so grant both.
    actions = ["ses:SendEmail"]
    resources = [
      "arn:aws:ses:${var.region}:${local.acct}:identity/abheenash.com",
      "arn:aws:ses:${var.region}:${local.acct}:identity/${var.owner_email}",
    ]
  }
}

resource "aws_iam_role_policy" "digest" {
  name   = "${local.name}-digest-policy"
  role   = aws_iam_role.digest.id
  policy = data.aws_iam_policy_document.digest.json
}

resource "aws_lambda_function" "digest" {
  function_name    = "${local.name}-digest"
  role             = aws_iam_role.digest.arn
  runtime          = "python3.12"
  handler          = "lambda_function.handler"
  filename         = data.archive_file.digest.output_path
  source_code_hash = data.archive_file.digest.output_base64sha256
  timeout          = 30
  environment {
    variables = {
      APPS_TABLE  = aws_dynamodb_table.applications.name
      SES_SENDER  = "no-reply@abheenash.com"
      OWNER_EMAIL = var.owner_email
      DASH_URL    = "https://${var.domain}"
    }
  }
  tags = local.tags
}

resource "aws_cloudwatch_event_rule" "digest" {
  name                = "${local.name}-digest"
  schedule_expression = "cron(0 13 ? * MON *)" # Mondays 13:00 UTC
  tags                = local.tags
}

resource "aws_cloudwatch_event_target" "digest" {
  rule = aws_cloudwatch_event_rule.digest.name
  arn  = aws_lambda_function.digest.arn
}

resource "aws_lambda_permission" "digest" {
  statement_id  = "AllowEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.digest.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.digest.arn
}
