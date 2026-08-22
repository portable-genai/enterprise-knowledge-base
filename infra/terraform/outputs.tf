# outputs.tf : Values the app/operators need to wire settings.yaml after apply.
#
# These map 1:1 onto config/settings.yaml / config.py fields so a deploy is just
# "apply, then export these into the runtime environment".

output "project_id" {
  description = "The deployment project id."
  value       = var.project_id
}

output "region" {
  description = "The deployment region (validated against the residency allowlist)."
  value       = var.region
}

output "corpus_bucket" {
  description = "Single-region CMEK governed bucket for config and redacted projections."
  value       = google_storage_bucket.corpus.name
}

output "raw_source_bucket" {
  description = "Single-region CMEK raw-input bucket; pipeline has bucket-scoped read only."
  value       = google_storage_bucket.raw_sources.name
}

output "control_input_bucket" {
  description = "Publisher-owned versioned registry/ACL bucket; pipeline has bucket-scoped read only."
  value       = google_storage_bucket.control_inputs.name
}

output "corpus_registry_uri" {
  description = "Exact reviewed registry object the refresh job reads."
  value       = var.corpus_registry_uri
}

output "acl_bindings_uri" {
  description = "Exact reviewed ACL authority object the refresh job reads."
  value       = var.acl_bindings_uri
}

output "artifact_registry_repository" {
  description = "Canonical regional Docker repository; release workflow emits image digests here."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
}

# --------------------------------- KMS -------------------------------------- #
output "kms_key" {
  description = "Regional CMEK crypto key id (settings.yaml kms_key / KB_KMS_KEY)."
  value       = google_kms_crypto_key.kb.id
}

# --------------------------------- AlloyDB ---------------------------------- #
output "alloydb_instance_uri" {
  description = "AlloyDB primary instance URI (settings.yaml alloydb.instance_uri)."
  value       = google_alloydb_instance.primary.name
}

output "alloydb_cluster" {
  description = "AlloyDB cluster resource name."
  value       = google_alloydb_cluster.kb.name
}

output "alloydb_iam_database_users" {
  description = "Per-workload IAM DB usernames to set as KB_ALLOYDB_USER; no passwords exist."
  value       = { for workload, user in google_alloydb_user.workload : workload => user.user_id }
}

output "migration_service_account" {
  description = "Dedicated passwordless IAM identity for audited 000/001 schema migrations."
  value       = google_service_account.migration.email
}

# ------------------------------- WORM logging ------------------------------- #
output "log_bucket" {
  description = "Locked WORM audit log bucket id (settings.yaml logging.bucket)."
  value       = google_logging_project_bucket_config.worm_audit.id
}

output "audit_sink_writer_identity" {
  description = "Sink writer identity (grant it bucket access if cross-project)."
  value       = google_logging_project_sink.audit_to_worm.writer_identity
}

# ----------------------------- Service accounts ----------------------------- #
output "app_service_account" {
  description = "Serving/API service account email."
  value       = google_service_account.app.email
}

output "pipeline_service_account" {
  description = "Ingestion/freshness pipeline service account email."
  value       = google_service_account.pipeline.email
}

output "scheduler_service_account" {
  description = "Corpus freshness scheduler service account email."
  value       = google_service_account.scheduler.email
}

output "refresh_job_name" {
  description = "Regional Cloud Run job to execute after migrations and artifact publication."
  value       = google_cloud_run_v2_job.freshness_refresh.name
}
