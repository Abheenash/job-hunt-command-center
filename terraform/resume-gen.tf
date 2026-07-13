#==============================================================================
# Résumé generator — POST /generate-resume
#
# A dedicated Lambda (separate from the CRUD api, since it uses a stronger model
# and a bigger prompt) that turns a pasted JD into a tailored 2-page / 4-project
# résumé (+ optional cover letter): Opus rewrites the content, the Lambda renders
# LaTeX and compiles a PDF (tectonic layer), with length auto-fit. Runs async.
#==============================================================================

data "archive_file" "resume_gen" {
  type        = "zip"
  source_dir  = "${path.module}/../src/resume_gen"
  output_path = "${path.module}/build/resume_gen.zip"
}

# tectonic (static LaTeX engine) as a layer, for server-side PDF compilation.
# Binary is fetched by scripts/fetch_tectonic_layer.sh (gitignored, ~25MB).
data "archive_file" "tectonic_layer" {
  type        = "zip"
  source_dir  = "${path.module}/layers/tectonic"
  output_path = "${path.module}/build/tectonic-layer.zip"
}

resource "aws_lambda_layer_version" "tectonic" {
  layer_name          = "${local.name}-tectonic"
  filename            = data.archive_file.tectonic_layer.output_path
  source_code_hash    = data.archive_file.tectonic_layer.output_base64sha256
  compatible_runtimes = ["python3.12"]
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
    # Scoped to the Claude family (résumé writer uses Opus via its cross-region profile).
    actions = ["bedrock:InvokeModel"]
    resources = [
      "arn:aws:bedrock:*::foundation-model/anthropic.claude-*",
      "arn:aws:bedrock:*:${local.acct}:inference-profile/us.anthropic.claude-*",
    ]
  }
  statement {
    sid       = "SnapshotReadResultsAndProfile"
    actions   = ["s3:PutObject", "s3:GetObject"]
    resources = ["${aws_s3_bucket.docs.arn}/generated/*", "${aws_s3_bucket.docs.arn}/profile/*"]
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
  timeout          = 300  # async worker: Opus (~35s) + tectonic compile/auto-fit
  memory_size      = 2048 # more memory = more CPU for the LaTeX compile
  layers           = [aws_lambda_layer_version.tectonic.arn]
  ephemeral_storage { size = 2048 } # tectonic package cache lives in /tmp
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

# Candidate-confirmed extra skills (ATS "I have this" -> future résumés carry it).
resource "aws_apigatewayv2_route" "profile_skills_get" {
  api_id             = aws_apigatewayv2_api.api.id
  route_key          = "GET /profile-skills"
  target             = "integrations/${aws_apigatewayv2_integration.resume_gen.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.jwt.id
}

resource "aws_apigatewayv2_route" "profile_skills_post" {
  api_id             = aws_apigatewayv2_api.api.id
  route_key          = "POST /profile-skills"
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
