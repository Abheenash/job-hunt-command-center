# --- Cognito: single-user auth ------------------------------------------------

resource "aws_cognito_user_pool" "users" {
  name                     = "${local.name}-users"
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  password_policy {
    minimum_length    = 10
    require_lowercase = true
    require_uppercase = true
    require_numbers   = true
    require_symbols   = false
  }

  tags = local.tags
}

resource "aws_cognito_user_pool_client" "web" {
  name         = "${local.name}-web"
  user_pool_id = aws_cognito_user_pool.users.id

  # Public SPA client (no secret). Direct username/password auth keeps the
  # front end self-contained — no hosted-UI redirects to wire up.
  generate_secret = false
  explicit_auth_flows = [
    "ALLOW_USER_PASSWORD_AUTH",
    "ALLOW_REFRESH_TOKEN_AUTH",
    "ALLOW_USER_SRP_AUTH",
  ]
  access_token_validity  = 1
  id_token_validity      = 1
  refresh_token_validity = 30
  token_validity_units {
    access_token  = "hours"
    id_token      = "hours"
    refresh_token = "days"
  }
}

# --- API Lambda (CRUD + presigned document URLs) ------------------------------

data "archive_file" "api" {
  type        = "zip"
  source_dir  = "${path.module}/../src/api"
  output_path = "${path.module}/build/api.zip"
}

resource "aws_iam_role" "api" {
  name               = "${local.name}-api-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = local.tags
}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy_attachment" "api_basic" {
  role       = aws_iam_role.api.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy_attachment" "api_xray" {
  role       = aws_iam_role.api.name
  policy_arn = "arn:aws:iam::aws:policy/AWSXRayDaemonWriteAccess"
}

data "aws_iam_policy_document" "api" {
  statement {
    sid     = "Ddb"
    actions = ["dynamodb:PutItem", "dynamodb:GetItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem", "dynamodb:Scan", "dynamodb:Query"]
    resources = [
      aws_dynamodb_table.applications.arn,
      aws_dynamodb_table.email_events.arn,
      "${aws_dynamodb_table.email_events.arn}/index/*",
    ]
  }
  statement {
    sid       = "DocsPresign"
    actions   = ["s3:PutObject", "s3:GetObject"]
    resources = ["${aws_s3_bucket.docs.arn}/*"]
  }
  # JD parsing via Amazon Bedrock (Claude Haiku) — scoped to the one model +
  # its cross-region inference profile.
  statement {
    sid     = "BedrockParseJD"
    actions = ["bedrock:InvokeModel"]
    resources = [
      "arn:aws:bedrock:*::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0",
      "arn:aws:bedrock:*:${local.acct}:inference-profile/us.anthropic.claude-haiku-4-5-20251001-v1:0",
    ]
  }
  # Openings Radar: read the scanned openings, flag tracked/dismissed, + rescan.
  statement {
    sid       = "OpeningsRead"
    actions   = ["dynamodb:Scan", "dynamodb:UpdateItem"]
    resources = [aws_dynamodb_table.openings.arn]
  }
  statement {
    sid       = "OpeningsScan"
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.openings_scan.arn]
  }
}

resource "aws_iam_role_policy" "api" {
  name   = "${local.name}-api-policy"
  role   = aws_iam_role.api.id
  policy = data.aws_iam_policy_document.api.json
}

resource "aws_lambda_function" "api" {
  function_name    = "${local.name}-api"
  role             = aws_iam_role.api.arn
  runtime          = "python3.12"
  handler          = "lambda_function.handler"
  filename         = data.archive_file.api.output_path
  source_code_hash = data.archive_file.api.output_base64sha256
  timeout          = 30
  tracing_config { mode = "Active" }
  environment {
    variables = {
      APPS_TABLE       = aws_dynamodb_table.applications.name
      EVENTS_TABLE     = aws_dynamodb_table.email_events.name
      DOCS_BUCKET      = aws_s3_bucket.docs.bucket
      OPENINGS_TABLE   = aws_dynamodb_table.openings.name
      OPENINGS_SCAN_FN = aws_lambda_function.openings_scan.function_name
    }
  }
  tags = local.tags
}

# --- HTTP API with a Cognito JWT authorizer -----------------------------------

resource "aws_apigatewayv2_api" "api" {
  name          = "${local.name}-api"
  protocol_type = "HTTP"
  cors_configuration {
    allow_origins = ["https://${aws_cloudfront_distribution.site.domain_name}", "https://${var.domain}", "http://localhost:8080"]
    allow_methods = ["GET", "POST", "PUT", "DELETE", "OPTIONS"]
    allow_headers = ["content-type", "authorization"]
    max_age       = 3000
  }
}

resource "aws_apigatewayv2_authorizer" "jwt" {
  api_id           = aws_apigatewayv2_api.api.id
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]
  name             = "${local.name}-cognito"
  jwt_configuration {
    audience = [aws_cognito_user_pool_client.web.id]
    issuer   = "https://cognito-idp.${var.region}.amazonaws.com/${aws_cognito_user_pool.users.id}"
  }
}

resource "aws_apigatewayv2_integration" "api" {
  api_id                 = aws_apigatewayv2_api.api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.api.invoke_arn
  payload_format_version = "2.0"
}

# Method-specific catch-all routes (the Lambda routes internally on path).
# Deliberately NOT a $default/ANY route: those would also match OPTIONS and send
# preflight through the JWT authorizer (401). By defining only GET/POST/PUT/DELETE,
# OPTIONS has no matching route, so API Gateway's automatic CORS answers preflight.
resource "aws_apigatewayv2_route" "api" {
  for_each           = toset(["GET", "POST", "PUT", "DELETE"])
  api_id             = aws_apigatewayv2_api.api.id
  route_key          = "${each.value} /{proxy+}"
  target             = "integrations/${aws_apigatewayv2_integration.api.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.jwt.id
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.api.id
  name        = "$default"
  auto_deploy = true
  default_route_settings {
    throttling_burst_limit = 10
    throttling_rate_limit  = 5
  }
}

resource "aws_lambda_permission" "api_gw" {
  statement_id  = "AllowAPIGWInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.api.execution_arn}/*/*"
}
