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
