#==============================================================================
# Self-monitoring — golden-signals dashboard, SLO-style alarms wired to a
# composite service-health alarm and an SNS topic, plus X-Ray tracing (enabled
# on the Lambdas + state machine in pipeline.tf / auth-api.tf). Reuses the
# patterns from the cloud-observability-sre project on this app's own stack.
#==============================================================================

# ---- Alert channel -----------------------------------------------------------
resource "aws_sns_topic" "alerts" {
  name = "${local.name}-alerts"
  tags = local.tags
}

resource "aws_sns_topic_subscription" "alerts_email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.owner_email # one-time: confirm via the email AWS sends
}

# ---- Bedrock spend budget (so Opus résumé generations can't surprise you) -----
# Isolates Amazon Bedrock cost from the rest of the account. Emails at 80% actual
# and 100% forecasted. Tune the ceiling with var.bedrock_budget_usd.
resource "aws_budgets_budget" "bedrock" {
  name         = "${local.name}-bedrock-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.bedrock_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  cost_filter {
    name   = "Service"
    values = ["Amazon Bedrock"]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.owner_email]
  }
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.owner_email]
  }
}

# ---- Alarms ------------------------------------------------------------------

# The single most important alarm: anything in the DLQ means a message failed
# 3x and is now quarantined — a human needs to look.
resource "aws_cloudwatch_metric_alarm" "dlq_not_empty" {
  alarm_name          = "${local.name}-dlq-not-empty"
  alarm_description   = "Emails failed processing 3x and landed in the DLQ. See docs/RUNBOOK.md."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = aws_sqs_queue.email_dlq.name }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
  tags                = local.tags
}

# The workflow itself failing (after its own retries).
resource "aws_cloudwatch_metric_alarm" "workflow_failed" {
  alarm_name          = "${local.name}-workflow-failed"
  alarm_description   = "process-email Step Functions executions are failing. See docs/RUNBOOK.md."
  namespace           = "AWS/States"
  metric_name         = "ExecutionsFailed"
  dimensions          = { StateMachineArn = aws_sfn_state_machine.process_email.arn }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  tags                = local.tags
}

# The dashboard/API serving 5xx to the browser.
resource "aws_cloudwatch_metric_alarm" "api_5xx" {
  alarm_name          = "${local.name}-api-5xx"
  alarm_description   = "The dashboard API is returning 5xx. See docs/RUNBOOK.md."
  namespace           = "AWS/ApiGateway"
  metric_name         = "5xx"
  dimensions          = { ApiId = aws_apigatewayv2_api.api.id, Stage = aws_apigatewayv2_stage.default.name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 5
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  tags                = local.tags
}

# One alarm covering unhandled errors across every pipeline function (metric math).
resource "aws_cloudwatch_metric_alarm" "pipeline_errors" {
  alarm_name          = "${local.name}-pipeline-errors"
  alarm_description   = "Unhandled errors in the scanner/dispatcher/classify/enrich functions. See docs/RUNBOOK.md."
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  tags                = local.tags

  metric_query {
    id          = "errs"
    expression  = "m_scanner + m_dispatcher + m_classify + m_enrich"
    label       = "PipelineErrors"
    return_data = true
  }
  dynamic "metric_query" {
    for_each = {
      m_scanner    = aws_lambda_function.scanner.function_name
      m_dispatcher = aws_lambda_function.dispatcher.function_name
      m_classify   = aws_lambda_function.classify.function_name
      m_enrich     = aws_lambda_function.enrich.function_name
    }
    content {
      id = metric_query.key
      metric {
        namespace   = "AWS/Lambda"
        metric_name = "Errors"
        dimensions  = { FunctionName = metric_query.value }
        period      = 300
        stat        = "Sum"
      }
    }
  }
}

# ---- Composite service-health alarm (single "is the app healthy?" signal) ----
resource "aws_cloudwatch_composite_alarm" "service_health" {
  alarm_name        = "${local.name}-service-health"
  alarm_description = "Rolls up every job-hunt alarm into one health signal. See docs/RUNBOOK.md."
  alarm_rule = join(" OR ", [
    "ALARM(${aws_cloudwatch_metric_alarm.dlq_not_empty.alarm_name})",
    "ALARM(${aws_cloudwatch_metric_alarm.workflow_failed.alarm_name})",
    "ALARM(${aws_cloudwatch_metric_alarm.api_5xx.alarm_name})",
    "ALARM(${aws_cloudwatch_metric_alarm.pipeline_errors.alarm_name})",
  ])
  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]
  tags          = local.tags
}

# ---- Golden-signals dashboard ------------------------------------------------
resource "aws_cloudwatch_dashboard" "health" {
  dashboard_name = "${local.name}-health"
  dashboard_body = jsonencode({
    widgets = [
      {
        type       = "text", x = 0, y = 0, width = 24, height = 2
        properties = { markdown = "# Job Hunt Command Center — service health\nEvent-driven inbox pipeline · API · workflow. Composite alarm: **${local.name}-service-health**. Runbook: `docs/RUNBOOK.md`." }
      },
      {
        type = "metric", x = 0, y = 2, width = 12, height = 6
        properties = {
          title  = "API — requests, 4xx, 5xx"
          region = var.region
          view   = "timeSeries"
          metrics = [
            ["AWS/ApiGateway", "Count", "ApiId", aws_apigatewayv2_api.api.id, "Stage", aws_apigatewayv2_stage.default.name, { stat = "Sum", label = "requests" }],
            ["AWS/ApiGateway", "4xx", "ApiId", aws_apigatewayv2_api.api.id, "Stage", aws_apigatewayv2_stage.default.name, { stat = "Sum", label = "4xx" }],
            ["AWS/ApiGateway", "5xx", "ApiId", aws_apigatewayv2_api.api.id, "Stage", aws_apigatewayv2_stage.default.name, { stat = "Sum", label = "5xx" }],
          ]
        }
      },
      {
        type = "metric", x = 12, y = 2, width = 12, height = 6
        properties = {
          title  = "API — latency (p50/p95/p99 ms)"
          region = var.region
          view   = "timeSeries"
          metrics = [
            ["AWS/ApiGateway", "Latency", "ApiId", aws_apigatewayv2_api.api.id, "Stage", aws_apigatewayv2_stage.default.name, { stat = "p50", label = "p50" }],
            ["...", { stat = "p95", label = "p95" }],
            ["...", { stat = "p99", label = "p99" }],
          ]
        }
      },
      {
        type = "metric", x = 0, y = 8, width = 12, height = 6
        properties = {
          title  = "Inbox queue — backlog & oldest-message age"
          region = var.region
          view   = "timeSeries"
          metrics = [
            ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", aws_sqs_queue.email.name, { stat = "Maximum", label = "queue depth" }],
            ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", aws_sqs_queue.email_dlq.name, { stat = "Maximum", label = "DLQ depth" }],
            ["AWS/SQS", "ApproximateAgeOfOldestMessage", "QueueName", aws_sqs_queue.email.name, { stat = "Maximum", label = "oldest age (s)", yAxis = "right" }],
          ]
        }
      },
      {
        type = "metric", x = 12, y = 8, width = 12, height = 6
        properties = {
          title  = "Workflow — process-email executions"
          region = var.region
          view   = "timeSeries"
          metrics = [
            ["AWS/States", "ExecutionsSucceeded", "StateMachineArn", aws_sfn_state_machine.process_email.arn, { stat = "Sum", label = "succeeded" }],
            ["AWS/States", "ExecutionsFailed", "StateMachineArn", aws_sfn_state_machine.process_email.arn, { stat = "Sum", label = "failed" }],
            ["AWS/States", "ExecutionTime", "StateMachineArn", aws_sfn_state_machine.process_email.arn, { stat = "Average", label = "avg time (ms)", yAxis = "right" }],
          ]
        }
      },
      {
        type = "metric", x = 0, y = 14, width = 12, height = 6
        properties = {
          title   = "Pipeline Lambdas — errors"
          region  = var.region
          view    = "timeSeries"
          stacked = true
          metrics = [
            ["AWS/Lambda", "Errors", "FunctionName", aws_lambda_function.scanner.function_name, { stat = "Sum", label = "scanner" }],
            ["AWS/Lambda", "Errors", "FunctionName", aws_lambda_function.dispatcher.function_name, { stat = "Sum", label = "dispatcher" }],
            ["AWS/Lambda", "Errors", "FunctionName", aws_lambda_function.classify.function_name, { stat = "Sum", label = "classify" }],
            ["AWS/Lambda", "Errors", "FunctionName", aws_lambda_function.enrich.function_name, { stat = "Sum", label = "enrich" }],
            ["AWS/Lambda", "Errors", "FunctionName", aws_lambda_function.api.function_name, { stat = "Sum", label = "api" }],
          ]
        }
      },
      {
        type = "metric", x = 12, y = 14, width = 12, height = 6
        properties = {
          title  = "Pipeline Lambdas — duration (avg ms)"
          region = var.region
          view   = "timeSeries"
          metrics = [
            ["AWS/Lambda", "Duration", "FunctionName", aws_lambda_function.scanner.function_name, { stat = "Average", label = "scanner" }],
            ["AWS/Lambda", "Duration", "FunctionName", aws_lambda_function.classify.function_name, { stat = "Average", label = "classify" }],
            ["AWS/Lambda", "Duration", "FunctionName", aws_lambda_function.enrich.function_name, { stat = "Average", label = "enrich" }],
            ["AWS/Lambda", "Duration", "FunctionName", aws_lambda_function.api.function_name, { stat = "Average", label = "api" }],
          ]
        }
      },
    ]
  })
}

output "dashboard_health_url" {
  value = "https://${var.region}.console.aws.amazon.com/cloudwatch/home?region=${var.region}#dashboards:name=${aws_cloudwatch_dashboard.health.dashboard_name}"
}
