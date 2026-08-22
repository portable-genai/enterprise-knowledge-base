# logging_worm.tf : WORM audit trail: locked Cloud Logging bucket + sink + audit config.
#
# General Principle map:
#   P-08 (immutable audit / WORM): the audit log is routed to a Cloud Logging bucket
#         whose retention is var.retention_days (~7 years) and whose explicitly approved
#         production lock makes it Write-Once-Read-Many. The audit adapter (cloud_logging_audit)
#         writes already-redacted AuditEvents here.
#   P-03 (residency): bucket location is asia-southeast1.
#   P-09 (CMEK explicit): the bucket is CMEK-encrypted (logging SA key binding in kms.tf).
#   P-04 (no raw PII in logs): only redacted prompts/responses are written (enforced
#         in the app); DATA_READ audit logging is enabled to record every read.
#
# ############################################################################ #

# The two system buckets are created with the project and their locations are immutable. The
# organization/folder Logging defaults therefore MUST exist before project creation. These import
# blocks make that foundation an executable precondition: a project whose bucket exists only at
# `global` has no matching Singapore import and the plan fails instead of claiming residency.
import {
  to = google_logging_project_bucket_config.default
  id = "projects/${var.project_id}/locations/${var.region}/buckets/_Default"
}

import {
  to = google_logging_project_bucket_config.required
  id = "projects/${var.project_id}/locations/${var.region}/buckets/_Required"
}

resource "google_logging_project_bucket_config" "default" {
  project   = var.project_id
  location  = var.region
  bucket_id = "_Default"

  cmek_settings {
    kms_key_name = google_kms_crypto_key.kb.id
  }

  depends_on = [google_kms_crypto_key_iam_member.logging]
}

resource "google_logging_project_bucket_config" "required" {
  project   = var.project_id
  location  = var.region
  bucket_id = "_Required"

  cmek_settings {
    kms_key_name = google_kms_crypto_key.kb.id
  }

  depends_on = [google_kms_crypto_key_iam_member.logging]
}
# # WARNING : LOCKING IS IRREVERSIBLE.                                        # #
# # Setting `lock_worm_bucket = true` permanently prevents reducing retention # #
# # deleting this bucket for the full retention window. You CANNOT undo it,   # #
# # not even with project-owner rights. Confirm retention_days before apply.  # #
# # The default unlocked demo posture is recoverable but not production.      # #
# ############################################################################ #

resource "google_logging_project_bucket_config" "worm_audit" {
  project        = var.project_id
  location       = var.region                       # asia-southeast1 (P-03)
  bucket_id      = "enterprise-knowledge-base-worm" # matches settings.yaml logging.bucket
  description    = "Governed audit bucket for A2 enterprise knowledge base (~7y retention; production locks explicitly)."
  retention_days = var.retention_days # 2557 (~7 years) by default

  # IRREVERSIBLE : see WARNING banner above. WORM governance requires this true.
  # Recoverable demo plans default to unlocked. Production mode refuses below unless the
  # operator separately and explicitly confirms this irreversible retention lock.
  locked = var.lock_worm_bucket

  lifecycle {
    precondition {
      condition     = !var.production_mode || var.lock_worm_bucket
      error_message = "production_mode requires lock_worm_bucket=true; the seven-year WORM lock is an explicit irreversible approval."
    }
  }

  # CMEK on the log bucket (P-09) : explicit, does not cascade.
  cmek_settings {
    kms_key_name = google_kms_crypto_key.kb.id
  }

  depends_on = [
    google_project_service.required,
    google_kms_crypto_key_iam_member.logging,
  ]
}

# Route the audit log stream into the locked WORM bucket.
resource "google_logging_project_sink" "audit_to_worm" {
  project     = var.project_id
  name        = "enterprise-knowledge-base-audit-to-worm"
  description = "Routes the enterprise-knowledge-base-audit log to the locked WORM bucket."

  # Destination is the locked logging bucket created above.
  destination = "logging.googleapis.com/${google_logging_project_bucket_config.worm_audit.id}"

  # Capture this app's audit log + all Cloud Audit Logs (admin/data access).
  filter = <<-EOT
    logName="projects/${var.project_id}/logs/enterprise-knowledge-base-audit"
    OR logName:"cloudaudit.googleapis.com"
  EOT

  unique_writer_identity = true
}

# The dedicated governed audit stream is already routed to the seven-year WORM bucket. Excluding
# only that log id from the system _Default sink avoids a second, shorter-lived copy; it does not
# suppress ingestion, the custom WORM sink, or mandatory _Required audit logs.
resource "google_logging_project_exclusion" "governed_audit_from_default" {
  project     = var.project_id
  name        = "enterprise-knowledge-base-audit-routed-to-worm"
  description = "Avoid duplicate storage after the governed audit stream enters the WORM bucket."
  filter      = "logName=\"projects/${var.project_id}/logs/enterprise-knowledge-base-audit\""

  depends_on = [google_logging_project_sink.audit_to_worm]
}

# The sink's unique service account can write logs, and nothing else, in this project. The
# destination remains the filtered locked bucket above; no application workload gets this role.
resource "google_project_iam_member" "audit_sink_bucket_writer" {
  project = var.project_id
  role    = "roles/logging.bucketWriter"
  member  = google_logging_project_sink.audit_to_worm.writer_identity
}

# --------------------------------------------------------------------------- #
# Enable Data Access audit logs (DATA_READ) so every read of the corpus, the
# ledger, and the audit store itself is itself audited (P-08). ADMIN_READ and
# DATA_WRITE are on by default; we add DATA_READ explicitly.
# --------------------------------------------------------------------------- #
resource "google_project_iam_audit_config" "data_access" {
  project = var.project_id
  service = "allServices"

  audit_log_config {
    log_type = "DATA_READ"
  }
  audit_log_config {
    log_type = "DATA_WRITE"
  }
  audit_log_config {
    log_type = "ADMIN_READ"
  }
}
