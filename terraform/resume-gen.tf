#==============================================================================
# Résumé generator — POST /generate-resume
#
# A dedicated Lambda (separate from the CRUD api, since it uses a stronger model
# and a bigger prompt) that turns a pasted JD into a tailored 2-page / 4-project
# résumé (+ optional cover letter) as LaTeX, scored and snapshotted to S3.
# Default model: Claude Sonnet; per-run Opus toggle from the UI.
#==============================================================================

data "archive_file" "resume_gen" {
  type        = "zip"
  source_dir  = "${path.module}/../src/resume_gen"
  output_path = "${path.module}/build/resume_gen.zip"
}

resource "aws_iam_role" "resume_gen" {
  name               = "${local.name}-resume-gen-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "resume_gen_basic" {
  role       = aws_iam_role.resume_gen.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy_attachment" "resume_gen_xray" {
  role       = aws_iam_role.resume_gen.name
  policy_arn = "arn:aws:iam::aws:policy/AWSXRayDaemonWriteAccess"
}

data "aws_iam_policy_document" "resume_gen" {
  statement {
    sid = "BedrockGenerate"
    # Scoped to the Claude family: the résumé writer legitimately switches between
    # Sonnet (default) and Opus (per-run toggle) via their cross-region profiles.
    actions = ["bedrock:InvokeModel"]
    resources = [
      "arn:aws:bedrock:*::foundation-model/anthropic.claude-*",
      "arn:aws:bedrock:*:${local.acct}:inference-profile/us.anthropic.claude-*",
    ]
  }
  statement {
    sid       = "SnapshotAndReadResults"
    actions   = ["s3:PutObject", "s3:GetObject"]
    resources = ["${aws_s3_bucket.docs.arn}/generated/*"]
  }
  statement {
    sid       = "SelfInvokeAsync" # Opus can exceed API GW's 30s cap → async worker
    actions   = ["lambda:InvokeFunction"]
    resources = ["arn:aws:lambda:${var.region}:${local.acct}:function:${local.name}-resume-gen"]
  }
}

resource "aws_iam_role_policy" "resume_gen" {
  name   = "${local.name}-resume-gen-policy"
  role   = aws_iam_role.resume_gen.id
  policy = data.aws_iam_policy_document.resume_gen.json
}

resource "aws_lambda_function" "resume_gen" {
  function_name    = "${local.name}-resume-gen"
  role             = aws_iam_role.resume_gen.arn
  runtime          = "python3.12"
  handler          = "lambda_function.handler"
  filename         = data.archive_file.resume_gen.output_path
  source_code_hash = data.archive_file.resume_gen.output_base64sha256
  timeout          = 120 # async worker: Opus full-rewrite runs ~30-45s
  memory_size      = 256
  tracing_config { mode = "Active" }
  environment {
    variables = {
      DOCS_BUCKET        = aws_s3_bucket.docs.bucket
      SELF_FUNCTION_NAME = "${local.name}-resume-gen"
    }
  }
  tags = local.tags
}

# Dedicated, more-specific route (wins over the CRUD `{proxy+}` route), same JWT gate.
resource "aws_apigatewayv2_integration" "resume_gen" {
  api_id                 = aws_apigatewayv2_api.api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.resume_gen.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "resume_gen" {
  api_id             = aws_apigatewayv2_api.api.id
  route_key          = "POST /generate-resume"
  target             = "integrations/${aws_apigatewayv2_integration.resume_gen.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.jwt.id
}

# Poll endpoint: the browser starts a job (POST) then polls this (GET ?job=<id>).
resource "aws_apigatewayv2_route" "resume_gen_status" {
  api_id             = aws_apigatewayv2_api.api.id
  route_key          = "GET /generate-resume"
  target             = "integrations/${aws_apigatewayv2_integration.resume_gen.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.jwt.id
}

resource "aws_lambda_permission" "resume_gen_gw" {
  statement_id  = "AllowAPIGWInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.resume_gen.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.api.execution_arn}/*/*"
}
