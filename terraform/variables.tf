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

variable "feed_url" {
  description = "Job-feed URLs for prospect ingestion (comma-separated; JSON or RSS/Atom). ToS-permitting sources only — LinkedIn/Indeed/Dice have no open feed. Empty = disabled."
  type        = string
  # Key-free default: The Muse "Software Engineering" (real jobs, real US employers).
  # For targeted, Indeed-sourced US results, swap in an Adzuna URL (free app_id/app_key).
  default = "https://www.themuse.com/api/public/jobs?category=Software%20Engineering&page=1,https://www.themuse.com/api/public/jobs?category=Software%20Engineering&page=2"
}

variable "feed_filter_keywords" {
  description = "Keep only postings whose TITLE mentions one of these (comma-separated). Empty = keep all."
  type        = string
  default     = "devops,cloud,sre,site reliability,platform,infrastructure,systems engineer,software engineer,backend,full stack,full-stack,kubernetes,aws,azure,gcp,solutions architect,sysadmin,network engineer,reliability,security engineer"
}
