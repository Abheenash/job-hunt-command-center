variable "region" {
  type    = string
  default = "us-east-1"
}

variable "name_prefix" {
  type    = string
  default = "jobhunt"
}

variable "owner_email" {
  description = "The single user's email (Cognito user + nudge/notification recipient)"
  type        = string
  default     = "abheenash007@gmail.com"
}

variable "domain" {
  description = "Custom domain for the dashboard"
  type        = string
  default     = "jobs.abheenash.com"
}

variable "hosted_zone_id" {
  description = "Route53 hosted zone for abheenash.com"
  type        = string
  default     = "Z05680081EH9N6652XDUI"
}

variable "bedrock_budget_usd" {
  description = "Monthly Amazon Bedrock spend ceiling (USD) that triggers email alerts"
  type        = number
  default     = 15
}
