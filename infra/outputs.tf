# These are the values that go into .env — printed so there is no clicking
# through the console to find them after an apply.

output "bigquery_project" {
  description = "Set BIGQUERY_PROJECT to this."
  value       = var.project_id
}

output "bigquery_dataset" {
  description = "Set BIGQUERY_DATASET to this."
  value       = google_bigquery_dataset.warehouse.dataset_id
}

output "lake_bucket" {
  description = "GCS bucket for Bronze and Silver."
  value       = google_storage_bucket.lake.name
}

output "dbt_service_account" {
  description = "Service account dbt authenticates as. Create a key for it, and point GOOGLE_APPLICATION_CREDENTIALS at the file."
  value       = google_service_account.dbt.email
}
