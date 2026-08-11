# The cloud side of RetailPulse, as code.
#
# Two resources and a service-account binding is not a large estate, and that is
# rather the point: it is small enough that clicking through the console would
# be faster once, and version-controlled infrastructure still wins the moment
# anyone needs to know why the dataset has a partition expiry, or to recreate
# the whole thing after a trial account lapses.
#
#   cd infra
#   terraform init
#   terraform plan  -var project_id=<your-project>
#   terraform apply -var project_id=<your-project>
#
# State is local by design. A remote backend is the right answer for a team and
# the wrong one for a portfolio project, whose reviewers should be able to run
# `plan` without being granted access to a state bucket first.

terraform {
  required_version = ">= 1.5"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# --- Object storage for Bronze and Silver -----------------------------------

resource "google_storage_bucket" "lake" {
  name     = "${var.project_id}-retailpulse-lake"
  location = var.region

  # Bronze is an immutable append-only log of API responses, so the bucket is
  # never a place anything gets edited in place.
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  versioning {
    enabled = true
  }

  # Bronze accumulates one snapshot per extraction run and is never compacted.
  # Ageing it into colder storage is what keeps a year of raw JSON from costing
  # anything meaningful, without ever deleting the lineage.
  lifecycle_rule {
    condition {
      age = 30
    }
    action {
      type          = "SetStorageClass"
      storage_class = "NEARLINE"
    }
  }

  lifecycle_rule {
    condition {
      age = 365
    }
    action {
      type          = "SetStorageClass"
      storage_class = "COLDLINE"
    }
  }

  labels = var.labels
}

# --- BigQuery -----------------------------------------------------------------

resource "google_bigquery_dataset" "warehouse" {
  dataset_id  = var.dataset_id
  location    = var.bigquery_location
  description = "RetailPulse Gold layer: dimensions, facts and KPI models built by dbt."

  # No default table expiry. These are the reporting marts; a table quietly
  # disappearing after N days is exactly the failure this project's freshness
  # checks exist to catch, and it would be self-inflicted.
  delete_contents_on_destroy = false

  labels = var.labels
}

# --- Service account for dbt --------------------------------------------------

resource "google_service_account" "dbt" {
  account_id   = "retailpulse-dbt"
  display_name = "RetailPulse dbt runner"
  description  = "Builds the Gold layer. Needs to read and write its own dataset and run jobs; nothing else."
}

# Scoped to the dataset rather than granted project-wide. dataEditor at project
# level would let this key rewrite every dataset in the project, which is a
# large blast radius for a key that lives on a laptop and in CI.
resource "google_bigquery_dataset_iam_member" "dbt_editor" {
  dataset_id = google_bigquery_dataset.warehouse.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.dbt.email}"
}

# Running a query is a project-level permission — there is no dataset-scoped
# equivalent — but jobUser only allows starting jobs, not reading data.
resource "google_project_iam_member" "dbt_job_user" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.dbt.email}"
}

resource "google_storage_bucket_iam_member" "dbt_lake" {
  bucket = google_storage_bucket.lake.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.dbt.email}"
}
