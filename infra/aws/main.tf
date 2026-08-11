# The AWS side of RetailPulse: an S3 bucket for the Silver lake, and a least-
# privilege identity for the pipeline to write it with.
#
# A separate Terraform root from `infra/`, not a second provider inside it, so
# the two clouds can be planned and applied independently. Someone reviewing
# the GCP estate should not have to hold AWS credentials to run `plan`, and a
# lapsed trial on one side should not block the other.
#
#   cd infra/aws
#   terraform init
#   terraform plan  -var bucket_name=<globally-unique-name>
#   terraform apply -var bucket_name=<globally-unique-name>
#
# State is local by design, for the same reason as the GCP root: a reviewer
# should be able to run `plan` without being granted access to a state bucket.

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.region
}

# --- The Silver lake --------------------------------------------------------

resource "aws_s3_bucket" "lake" {
  bucket = var.bucket_name

  tags = {
    Project   = "retailpulse"
    Layer     = "silver"
    ManagedBy = "terraform"
  }
}

# Silver is rebuilt in full on every run and is derived data — Bronze is the
# system of record. Versioning is still on, because the failure this protects
# against is not losing Silver but overwriting it with a bad rebuild while the
# Bronze that produced the good one is still sitting there.
resource "aws_s3_bucket_versioning" "lake" {
  bucket = aws_s3_bucket.lake.id
  versioning_configuration {
    status = "Enabled"
  }
}

# Nothing here is ever public. This bucket holds a real store's sales history.
resource "aws_s3_bucket_public_access_block" "lake" {
  bucket                  = aws_s3_bucket.lake.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "lake" {
  bucket = aws_s3_bucket.lake.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Old versions are for recovering from a bad rebuild, which is noticed in days
# rather than months. Expiring them keeps a year of rewrites from accumulating
# silently.
resource "aws_s3_bucket_lifecycle_configuration" "lake" {
  bucket = aws_s3_bucket.lake.id

  rule {
    id     = "expire-noncurrent-silver"
    status = "Enabled"
    filter {}

    noncurrent_version_expiration {
      noncurrent_days = 30
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

# --- Least-privilege identity for the pipeline ------------------------------
#
# Scoped to this one bucket and to the operations the pipeline actually
# performs: DuckDB writes Parquet with PutObject and reads it back with
# GetObject, and dbt's source resolution lists the prefix. No DeleteBucket, no
# access to anything else in the account.

data "aws_iam_policy_document" "lake_readwrite" {
  statement {
    sid       = "ListTheLakeBucket"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [aws_s3_bucket.lake.arn]
  }

  statement {
    sid       = "ReadWriteSilverObjects"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.lake.arn}/*"]
  }
}

resource "aws_iam_policy" "lake_readwrite" {
  name        = "${var.bucket_name}-readwrite"
  description = "Read and write the RetailPulse Silver lake, and nothing else."
  policy      = data.aws_iam_policy_document.lake_readwrite.json
}

resource "aws_iam_user" "pipeline" {
  name = "${var.bucket_name}-pipeline"
  tags = {
    Project   = "retailpulse"
    ManagedBy = "terraform"
  }
}

resource "aws_iam_user_policy_attachment" "pipeline" {
  user       = aws_iam_user.pipeline.name
  policy_arn = aws_iam_policy.lake_readwrite.arn
}

# No aws_iam_access_key resource on purpose. Terraform would write the secret
# into local state in plaintext, and this project's whole posture is that
# credentials never land in a file anyone might commit. Create the key with
# `aws iam create-access-key --user-name <the output below>` and put it in
# .env, or attach the policy to a role and skip long-lived keys entirely.
