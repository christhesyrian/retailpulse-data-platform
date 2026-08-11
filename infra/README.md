# Infrastructure

The GCP side of RetailPulse: a GCS bucket for Bronze/Silver, a BigQuery dataset
for the Gold layer, and a least-privilege service account for dbt.

```bash
cd infra
cp example.tfvars terraform.tfvars     # then fill in project_id
terraform init
terraform plan
terraform apply
```

## Credentials

`terraform` and `dbt` authenticate as **different identities on purpose**.

dbt runs as the `retailpulse-dbt` service account, which can read and write one
dataset and start query jobs — and nothing else. That key lives on a laptop and
in CI, so its blast radius is the thing worth minimising.

Terraform creates service accounts and IAM bindings, which that key deliberately
cannot do. Run it as yourself:

```bash
gcloud auth application-default login
```

If `GOOGLE_APPLICATION_CREDENTIALS` is exported for dbt, unset it for the
Terraform commands or it will authenticate as the low-privilege account and fail
on permissions in a way that reads like a bug.

## Importing what already exists

The service account and dataset were created by hand before this config existed,
which is the usual order of events and the usual source of the next problem:
`apply` will try to create them again and fail with "already exists".

Bring them under management rather than deleting and recreating:

```bash
terraform import google_service_account.dbt \
  projects/<project>/serviceAccounts/retailpulse-dbt@<project>.iam.gserviceaccount.com

terraform import google_bigquery_dataset.warehouse \
  projects/<project>/datasets/retailpulse
```

Then `terraform plan` should report no changes for those two, and only propose
creating the bucket. A plan that proposes *destroying* either of them means the
import did not take — stop and re-import rather than applying.

## Why the resources look the way they do

- **The bucket ages Bronze into NEARLINE at 30 days and COLDLINE at 365.**
  Bronze is append-only and never compacted, so it grows forever by design.
  Ageing it is what keeps a year of raw JSON costing cents; nothing is deleted,
  because the lineage is the point.
- **Versioning is on and public access is blocked.** Bronze is an immutable
  record of what an external API returned, and the whole privacy posture of this
  project rests on real business data never being publicly reachable.
- **dbt's dataEditor is scoped to the dataset, not the project.** Project-level
  would let a laptop key rewrite every dataset in the project.
- **`bigquery_location` is separate from `region`.** A BigQuery location is a
  multi-region (`US`) or a region (`us-west1`), and a dataset cannot be moved
  after creation — so conflating the two is a mistake you make exactly once.
- **State is local.** A remote backend is right for a team and wrong here:
  anyone reviewing this repo should be able to run `plan` without first being
  granted access to a state bucket.
