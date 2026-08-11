variable "bucket_name" {
  description = <<-DESC
    Globally unique S3 bucket name for the Silver lake. S3 bucket names are a
    single global namespace, so this cannot default to something tidy like
    "retailpulse-lake" — that name is almost certainly taken by someone else.
  DESC
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.bucket_name))
    error_message = "Bucket names must be 3-63 characters, lowercase letters, digits, hyphens or dots."
  }
}

variable "region" {
  description = "AWS region for the bucket. Keep it near whatever reads the lake."
  type        = string
  default     = "us-east-1"
}
