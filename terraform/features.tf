#==============================================================================
# On-demand job-search feature Lambdas (deep-eval, research, interview prep,
# outreach drafts, cover letters, offer/negotiation tools).
#
# Each is a small, synchronous Lambda behind the same Cognito-JWT HTTP API as the
# rest of the app, routed by a dedicated path (more specific than the CRUD proxy
# route). Deterministic features (offer/outreach/interview) need no Bedrock; the
# AI features (evaluate/cover) get a Claude-scoped bedrock:InvokeModel policy and
# fall back to deterministic output if the model is unavailable.
#==============================================================================

locals {
  features = {
    evaluate  = { bedrock = true, timeout = 60, memory = 512 }
    cover     = { bedrock = true, timeout = 60, memory = 512 }
    offer     = { bedrock = false, timeout = 10, memory = 128 }
    outreach  = { bedrock = false, timeout = 10, memory = 128 }
    interview = { bedrock = false, timeout = 10, memory = 128 }
  }
  # route_key => which feature Lambda serves it (all JWT-gated, all AWS_PROXY)
  feature_routes = {
    "POST /evaluate"           = "evaluate"
    "POST /research"           = "evaluate"
    "POST /cover"              = "cover"
    "POST /offer/salary-gap"   = "offer"
    "POST /offer/clause-walk"  = "offer"
    "POST /offer/scripts"      = "offer"
    "POST /outreach"           = "outreach"
    "POST /interview/stories"  = "interview"
    "POST /interview/redflags" = "interview"
    "POST /interview/plan"     = "interview"
    "POST /interview/debrief"  = "interview"
  }
  features_bedrock = { for k, v in local.features : k => v if v.bedrock }
}

data "archive_file" "feature" {
  for_each    = local.features
  type        = "zip"
  source_dir  = "${path.module}/../src/${each.key}"
  output_path = "${path.module}/build/${each.key}.zip"
}

resource "aws_iam_role" "feature" {
  for_each           = local.features
  name               = "${local.name}-${each.key}-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "feature_basic" {
  for_each   = local.features
  role       = aws_iam_role.feature[each.key].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy_attachment" "feature_xray" {
  for_each   = local.features
  role       = aws_iam_role.feature[each.key].name
  policy_arn = "arn:aws:iam::aws:policy/AWSXRayDaemonWriteAccess"
}

# Bedrock invoke — only for the AI features, scoped to the Claude family (+ its
# cross-region inference profiles), exactly like the résumé generator.
data "aws_iam_policy_document" "feature_bedrock" {
  for_each = local.features_bedrock
  statement {
    sid     = "BedrockInvoke"
    actions = ["bedrock:InvokeModel"]
    resources = [
      "arn:aws:bedrock:*::foundation-model/anthropic.claude-*",
      "arn:aws:bedrock:*:${local.acct}:inference-profile/us.anthropic.claude-*",
    ]
  }
}

resource "aws_iam_role_policy" "feature_bedrock" {
  for_each = local.features_bedrock
  name     = "${local.name}-${each.key}-bedrock"
  role     = aws_iam_role.feature[each.key].id
  policy   = data.aws_iam_policy_document.feature_bedrock[each.key].json
}

resource "aws_lambda_function" "feature" {
  for_each         = local.features
  function_name    = "${local.name}-${each.key}"
  role             = aws_iam_role.feature[each.key].arn
  runtime          = "python3.12"
  handler          = "lambda_function.handler"
  filename         = data.archive_file.feature[each.key].output_path
  source_code_hash = data.archive_file.feature[each.key].output_base64sha256
  timeout          = each.value.timeout
  memory_size      = each.value.memory
  tracing_config { mode = "Active" }
  tags = local.tags
}

resource "aws_apigatewayv2_integration" "feature" {
  for_each               = local.features
  api_id                 = aws_apigatewayv2_api.api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.feature[each.key].invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "feature" {
  for_each           = local.feature_routes
  api_id             = aws_apigatewayv2_api.api.id
  route_key          = each.key
  target             = "integrations/${aws_apigatewayv2_integration.feature[each.value].id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.jwt.id
}

resource "aws_lambda_permission" "feature_gw" {
  for_each      = local.features
  statement_id  = "AllowAPIGWInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.feature[each.key].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.api.execution_arn}/*/*"
}
