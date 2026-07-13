#==============================================================================
# Event-driven inbox pipeline
#
#   EventBridge (rate 6h)
#     -> Scanner Lambda        (IMAP read -> dedupe -> pre-filter -> SQS)
#        -> SQS main queue  --redrive(3x)-->  SQS DLQ
#           -> Dispatcher Lambda  (SQS trigger, partial-batch failure reporting)
#              -> Step Functions Express "process-email"
#                   Classify (Bedrock)  ->  Enrich (match + auto-advance + record)
#
# Why this shape: the expensive/failure-prone work (Bedrock, DynamoDB) is off the
# ingest path, so a slow mailbox can't stall it; SQS buffers + retries per message
# and quarantines poison messages in the DLQ; Step Functions gives each step its
# own retry policy and a visible execution history.
#==============================================================================

# ---- X-Ray: shared managed policy for every function in the pipeline ---------
locals {
  xray_policy_arn = "arn:aws:iam::aws:policy/AWSXRayDaemonWriteAccess"
}

# ---- SQS: main processing queue + dead-letter queue --------------------------
resource "aws_sqs_queue" "email_dlq" {
  name                      = "${local.name}-email-dlq"
  message_retention_seconds = 1209600 # 14 days — time to inspect/redrive failures
  sqs_managed_sse_enabled   = true
  tags                      = local.tags
}

resource "aws_sqs_queue" "email" {
  name                       = "${local.name}-email-processing"
  visibility_timeout_seconds = 360 # >= 6x dispatcher timeout (60s)
  message_retention_seconds  = 345600
  sqs_managed_sse_enabled    = true
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.email_dlq.arn
    maxReceiveCount     = 3
  })
  tags = local.tags
}

#------------------------------------------------------------------------------
# Scanner Lambda (EventBridge-scheduled ingest edge)
#------------------------------------------------------------------------------
data "archive_file" "scanner" {
  type        = "zip"
  source_dir  = "${path.module}/../src/scanner"
  output_path = "${path.module}/build/scanner.zip"
}

resource "aws_iam_role" "scanner" {
  name               = "${local.name}-scanner-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "scanner_basic" {
  role       = aws_iam_role.scanner.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy_attachment" "scanner_xray" {
  role       = aws_iam_role.scanner.name
  policy_arn = local.xray_policy_arn
}

data "aws_iam_policy_document" "scanner" {
  statement {
    actions   = ["dynamodb:Scan"] # existing event ids for idempotency
    resources = [aws_dynamodb_table.email_events.arn]
  }
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.email.arn]
  }
  statement {
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.email.arn]
  }
}

resource "aws_iam_role_policy" "scanner" {
  name   = "${local.name}-scanner-policy"
  role   = aws_iam_role.scanner.id
  policy = data.aws_iam_policy_document.scanner.json
}

resource "aws_lambda_function" "scanner" {
  function_name    = "${local.name}-scanner"
  role             = aws_iam_role.scanner.arn
  runtime          = "python3.12"
  handler          = "lambda_function.handler"
  filename         = data.archive_file.scanner.output_path
  source_code_hash = data.archive_file.scanner.output_base64sha256
  timeout          = 120
  tracing_config { mode = "Active" }
  environment {
    variables = {
      EVENTS_TABLE = aws_dynamodb_table.email_events.name
      SECRET_ID    = aws_secretsmanager_secret.email.arn
      QUEUE_URL    = aws_sqs_queue.email.url
    }
  }
  tags = local.tags
}

resource "aws_cloudwatch_event_rule" "scanner" {
  name                = "${local.name}-scanner"
  schedule_expression = "rate(6 hours)"
  tags                = local.tags
}

resource "aws_cloudwatch_event_target" "scanner" {
  rule = aws_cloudwatch_event_rule.scanner.name
  arn  = aws_lambda_function.scanner.arn
}

resource "aws_lambda_permission" "scanner" {
  statement_id  = "AllowEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.scanner.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.scanner.arn
}

#------------------------------------------------------------------------------
# Classify Lambda (workflow state 1 — Bedrock triage)
#------------------------------------------------------------------------------
data "archive_file" "classify" {
  type        = "zip"
  source_dir  = "${path.module}/../src/classify"
  output_path = "${path.module}/build/classify.zip"
}

resource "aws_iam_role" "classify" {
  name               = "${local.name}-classify-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "classify_basic" {
  role       = aws_iam_role.classify.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy_attachment" "classify_xray" {
  role       = aws_iam_role.classify.name
  policy_arn = local.xray_policy_arn
}

data "aws_iam_policy_document" "classify" {
  statement {
    # AI email triage (Claude Haiku) — scoped to the one model + inference profile
    actions = ["bedrock:InvokeModel"]
    resources = [
      "arn:aws:bedrock:*::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0",
      "arn:aws:bedrock:*:${local.acct}:inference-profile/us.anthropic.claude-haiku-4-5-20251001-v1:0",
    ]
  }
}

resource "aws_iam_role_policy" "classify" {
  name   = "${local.name}-classify-policy"
  role   = aws_iam_role.classify.id
  policy = data.aws_iam_policy_document.classify.json
}

resource "aws_lambda_function" "classify" {
  function_name    = "${local.name}-classify"
  role             = aws_iam_role.classify.arn
  runtime          = "python3.12"
  handler          = "lambda_function.handler"
  filename         = data.archive_file.classify.output_path
  source_code_hash = data.archive_file.classify.output_base64sha256
  timeout          = 30
  tracing_config { mode = "Active" }
  tags = local.tags
}

#------------------------------------------------------------------------------
# Enrich Lambda (workflow state 2 — match, auto-advance, record)
#------------------------------------------------------------------------------
data "archive_file" "enrich" {
  type        = "zip"
  source_dir  = "${path.module}/../src/enrich"
  output_path = "${path.module}/build/enrich.zip"
}

resource "aws_iam_role" "enrich" {
  name               = "${local.name}-enrich-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "enrich_basic" {
  role       = aws_iam_role.enrich.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy_attachment" "enrich_xray" {
  role       = aws_iam_role.enrich.name
  policy_arn = local.xray_policy_arn
}

data "aws_iam_policy_document" "enrich" {
  statement {
    # read apps to match, and update them to auto-advance status from email
    actions   = ["dynamodb:Scan", "dynamodb:GetItem", "dynamodb:PutItem"]
    resources = [aws_dynamodb_table.applications.arn]
  }
  statement {
    actions   = ["dynamodb:PutItem"] # write the classified event
    resources = [aws_dynamodb_table.email_events.arn]
  }
}

resource "aws_iam_role_policy" "enrich" {
  name   = "${local.name}-enrich-policy"
  role   = aws_iam_role.enrich.id
  policy = data.aws_iam_policy_document.enrich.json
}

resource "aws_lambda_function" "enrich" {
  function_name    = "${local.name}-enrich"
  role             = aws_iam_role.enrich.arn
  runtime          = "python3.12"
  handler          = "lambda_function.handler"
  filename         = data.archive_file.enrich.output_path
  source_code_hash = data.archive_file.enrich.output_base64sha256
  timeout          = 30
  tracing_config { mode = "Active" }
  environment {
    variables = {
      APPS_TABLE   = aws_dynamodb_table.applications.name
      EVENTS_TABLE = aws_dynamodb_table.email_events.name
    }
  }
  tags = local.tags
}

#------------------------------------------------------------------------------
# Step Functions Express state machine "process-email"
#------------------------------------------------------------------------------
resource "aws_cloudwatch_log_group" "sfn" {
  name              = "/aws/vendedlogs/states/${local.name}-process-email"
  retention_in_days = 30
  tags              = local.tags
}

resource "aws_iam_role" "sfn" {
  name = "${local.name}-process-email-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "states.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  tags = local.tags
}

data "aws_iam_policy_document" "sfn" {
  statement {
    sid       = "InvokeTasks"
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.classify.arn, aws_lambda_function.enrich.arn]
  }
  statement {
    sid = "XRay"
    actions = [
      "xray:PutTraceSegments", "xray:PutTelemetryRecords",
      "xray:GetSamplingRules", "xray:GetSamplingTargets",
    ]
    resources = ["*"]
  }
  statement {
    sid = "ExpressLogging" # vended-logs actions don't support resource scoping
    actions = [
      "logs:CreateLogDelivery", "logs:GetLogDelivery", "logs:UpdateLogDelivery",
      "logs:DeleteLogDelivery", "logs:ListLogDeliveries", "logs:PutResourcePolicy",
      "logs:DescribeResourcePolicies", "logs:DescribeLogGroups",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "sfn" {
  name   = "${local.name}-process-email-policy"
  role   = aws_iam_role.sfn.id
  policy = data.aws_iam_policy_document.sfn.json
}

resource "aws_sfn_state_machine" "process_email" {
  name     = "${local.name}-process-email"
  type     = "EXPRESS"
  role_arn = aws_iam_role.sfn.arn

  definition = jsonencode({
    Comment = "Classify one email with Bedrock, then link/enrich the application."
    StartAt = "Classify"
    States = {
      Classify = {
        Type       = "Task"
        Resource   = "arn:aws:states:::lambda:invoke"
        Parameters = { FunctionName = aws_lambda_function.classify.arn, "Payload.$" = "$" }
        OutputPath = "$.Payload"
        Retry = [{
          ErrorEquals     = ["Lambda.TooManyRequestsException", "Lambda.ServiceException", "States.TaskFailed"]
          IntervalSeconds = 2, MaxAttempts = 3, BackoffRate = 2.0
        }]
        Next = "Enrich"
      }
      Enrich = {
        Type       = "Task"
        Resource   = "arn:aws:states:::lambda:invoke"
        Parameters = { FunctionName = aws_lambda_function.enrich.arn, "Payload.$" = "$" }
        OutputPath = "$.Payload"
        Retry = [{
          ErrorEquals     = ["Lambda.TooManyRequestsException", "Lambda.ServiceException", "States.TaskFailed"]
          IntervalSeconds = 2, MaxAttempts = 3, BackoffRate = 2.0
        }]
        End = true
      }
    }
  })

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.sfn.arn}:*"
    include_execution_data = true
    level                  = "ERROR"
  }

  tracing_configuration { enabled = true }

  tags = local.tags
}

#------------------------------------------------------------------------------
# Dispatcher Lambda (SQS -> Step Functions bridge)
#------------------------------------------------------------------------------
data "archive_file" "dispatcher" {
  type        = "zip"
  source_dir  = "${path.module}/../src/dispatcher"
  output_path = "${path.module}/build/dispatcher.zip"
}

resource "aws_iam_role" "dispatcher" {
  name               = "${local.name}-dispatcher-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "dispatcher_basic" {
  role       = aws_iam_role.dispatcher.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy_attachment" "dispatcher_xray" {
  role       = aws_iam_role.dispatcher.name
  policy_arn = local.xray_policy_arn
}

data "aws_iam_policy_document" "dispatcher" {
  statement {
    sid       = "ConsumeQueue"
    actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
    resources = [aws_sqs_queue.email.arn]
  }
  statement {
    sid       = "RunWorkflow"
    actions   = ["states:StartSyncExecution"]
    resources = [aws_sfn_state_machine.process_email.arn]
  }
}

resource "aws_iam_role_policy" "dispatcher" {
  name   = "${local.name}-dispatcher-policy"
  role   = aws_iam_role.dispatcher.id
  policy = data.aws_iam_policy_document.dispatcher.json
}

resource "aws_lambda_function" "dispatcher" {
  function_name    = "${local.name}-dispatcher"
  role             = aws_iam_role.dispatcher.arn
  runtime          = "python3.12"
  handler          = "lambda_function.handler"
  filename         = data.archive_file.dispatcher.output_path
  source_code_hash = data.archive_file.dispatcher.output_base64sha256
  timeout          = 60
  tracing_config { mode = "Active" }
  environment {
    variables = {
      STATE_MACHINE_ARN = aws_sfn_state_machine.process_email.arn
    }
  }
  tags = local.tags
}

resource "aws_lambda_event_source_mapping" "dispatcher" {
  event_source_arn                   = aws_sqs_queue.email.arn
  function_name                      = aws_lambda_function.dispatcher.arn
  batch_size                         = 5
  maximum_batching_window_in_seconds = 5
  function_response_types            = ["ReportBatchItemFailures"]
}
