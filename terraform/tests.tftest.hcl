mock_provider "aws" {
  # redrive_policy is jsonencode()'d around the DLQ's computed ARN, so the whole
  # policy string is unknown at plan unless the ARN is made concrete.
  override_resource {
    target          = aws_sqs_queue.email_dlq
    override_during = plan
    values          = { arn = "arn:aws:sqs:us-east-1:111122223333:jobhunt-email-dlq" }
  }
  # domain.tf does `for_each` over the certificate's validation options, which are
  # unknown at plan time against a mock — and a for_each over an unknown value is
  # a hard error, not a deferred one. Making them concrete lets the plan complete.
  # CloudFront validates that the cert ARN looks like an ARN; a mock returns a
  # random id, which the provider rejects before any assertion can run.
  override_resource {
    target          = aws_acm_certificate_validation.site
    override_during = plan
    values = {
      certificate_arn = "arn:aws:acm:us-east-1:111122223333:certificate/00000000-0000-0000-0000-000000000000"
    }
  }
  override_resource {
    target          = aws_acm_certificate.site
    override_during = plan
    values = {
      arn = "arn:aws:acm:us-east-1:111122223333:certificate/00000000-0000-0000-0000-000000000000"
      domain_validation_options = [{
        domain_name           = "example.invalid"
        resource_record_name  = "_test.example.invalid."
        resource_record_type  = "CNAME"
        resource_record_value = "_test.acm-validations.aws."
      }]
    }
  }
  # Mocked data sources return placeholder strings that the AWS provider then
  # rejects as invalid JSON; a minimal valid document keeps the mock usable.
  override_data {
    target = data.aws_iam_policy_document.api
    values = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
  }
  override_data {
    target = data.aws_iam_policy_document.classify
    values = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
  }
  override_data {
    target = data.aws_iam_policy_document.digest
    values = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
  }
  override_data {
    target = data.aws_iam_policy_document.dispatcher
    values = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
  }
  override_data {
    target = data.aws_iam_policy_document.enrich
    values = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
  }
  override_data {
    target = data.aws_iam_policy_document.feature_bedrock
    values = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
  }
  override_data {
    target = data.aws_iam_policy_document.lambda_assume
    values = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
  }
  override_data {
    target = data.aws_iam_policy_document.nudge
    values = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
  }
  override_data {
    target = data.aws_iam_policy_document.openings_scan
    values = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
  }
  override_data {
    target = data.aws_iam_policy_document.resume_gen
    values = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
  }
  override_data {
    target = data.aws_iam_policy_document.scanner
    values = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
  }
  override_data {
    target = data.aws_iam_policy_document.sfn
    values = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
  }
  override_data {
    target = data.aws_iam_policy_document.site
    values = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
  }
}
mock_provider "archive" {}
run "the_email_queue_has_a_dead_letter_queue" {
  command = plan

  # The whole point of decomposing the scanner into a queue was poison-message
  # isolation. A queue without a redrive policy retries a bad message forever and
  # blocks everything behind it — which is the failure this design exists to stop.
  assert {
    condition     = can(jsondecode(aws_sqs_queue.email.redrive_policy))
    error_message = "The email queue must have a redrive policy. Without one a single unparseable message retries forever and stalls the pipeline."
  }

  assert {
    condition     = jsondecode(aws_sqs_queue.email.redrive_policy)["maxReceiveCount"] <= 5
    error_message = "maxReceiveCount should be small. A high count just delays the moment a poison message reaches the DLQ where it can be seen."
  }
}

run "private_buckets_are_private" {
  command = plan

  # The docs bucket holds resumes. This is personal data in a public repo's
  # infrastructure, so the assertion is worth having twice over.
  assert {
    condition = alltrue([
      aws_s3_bucket_public_access_block.docs.block_public_acls,
      aws_s3_bucket_public_access_block.docs.block_public_policy,
      aws_s3_bucket_public_access_block.docs.ignore_public_acls,
      aws_s3_bucket_public_access_block.docs.restrict_public_buckets,
    ])
    error_message = "The documents bucket holds resumes and must block every public-access vector."
  }
}
