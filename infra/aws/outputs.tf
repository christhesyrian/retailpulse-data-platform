output "silver_dir" {
  description = "Set RETAILPULSE_SILVER_DIR to this to point the pipeline at S3."
  value       = "s3://${aws_s3_bucket.lake.bucket}/silver"
}

output "bucket_arn" {
  value = aws_s3_bucket.lake.arn
}

output "pipeline_user" {
  description = <<-DESC
    The IAM user the pipeline should authenticate as. Terraform deliberately
    does not create an access key for it — that would put the secret in local
    state as plaintext. Create one with:

      aws iam create-access-key --user-name <this value>
  DESC
  value       = aws_iam_user.pipeline.name
}
