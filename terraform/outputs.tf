output "api_endpoint" {
  value = aws_apigatewayv2_api.api.api_endpoint
}

output "user_pool_id" {
  value = aws_cognito_user_pool.users.id
}

output "user_pool_client_id" {
  value = aws_cognito_user_pool_client.web.id
}

output "site_bucket" {
  value = aws_s3_bucket.site.bucket
}

output "docs_bucket" {
  value = aws_s3_bucket.docs.bucket
}

output "dashboard_url" {
  value = "https://${aws_cloudfront_distribution.site.domain_name}"
}
