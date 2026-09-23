# Remote state, as a PARTIAL configuration.
#
# The values live in backend.hcl (gitignored) rather than here, because the bucket
# name embeds an account id. Initialise with:
#
#     cp backend.hcl.example backend.hcl   # then fill it in
#     terraform init -backend-config=backend.hcl
#
# The bucket itself is created once by aws-landing-zone/bootstrap, and shared by
# every project — one bucket to version, encrypt and audit instead of nine.
#
# Locking is S3-native (use_lockfile), which replaced the DynamoDB lock table in
# Terraform 1.10. CI runs `terraform init -backend=false` for fmt/validate/test,
# so none of this is needed just to check a pull request.
terraform {
  backend "s3" {}
}
