variable "project_id" {
  description = "GCP project that owns the dataset and bucket."
  type        = string
}

variable "region" {
  description = "Region for the storage bucket."
  type        = string
  default     = "us-west1"
}

variable "bigquery_location" {
  description = <<-EOT
    BigQuery dataset location. Deliberately separate from `region`: a BigQuery
    location is a multi-region like "US" or a region like "us-west1", and a
    dataset cannot be moved after creation, so conflating the two is a mistake
    you only get to make once.
  EOT
  type        = string
  default     = "US"
}

variable "dataset_id" {
  description = "BigQuery dataset holding the Gold layer."
  type        = string
  default     = "retailpulse"
}

variable "labels" {
  description = "Applied to every billable resource, so cost can be attributed."
  type        = map(string)
  default = {
    project    = "retailpulse"
    managed_by = "terraform"
  }
}
