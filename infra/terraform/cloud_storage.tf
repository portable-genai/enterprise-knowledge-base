# cloud_storage.tf : Single-region, CMEK-encrypted corpus bucket.
#
# General Principle map:
#   P-03 (residency): the bucket is a SINGLE-region bucket in asia-southeast1, never
#         multi-region/dual-region, so the redacted document bytes never leave Singapore.
#   P-04 (redact-before-store): only already-redacted documents are written here by the
#         ingestion adapter (DLP runs upstream); the bucket holds no raw PII.
#   P-09 (CMEK explicit): the bucket is CMEK-encrypted (storage SA key binding in kms.tf).
#
# The portable parser extracts in memory; only redacted text is persisted here so the governed
# store has a single, in-region, encrypted copy and raw source bytes never cross persistence.

resource "google_storage_bucket" "corpus" {
  # Project-derived suffix makes the globally-scoped bucket name deployment-unique.
  name     = "enterprise-knowledge-base-corpus-${var.project_id}"
  project  = var.project_id
  location = var.region # SINGLE-region Singapore (P-03), never multi-region

  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  # CMEK on the bucket (P-09) : explicit, does not cascade.
  encryption {
    default_kms_key_name = google_kms_crypto_key.kb.id
  }

  # Keep prior versions so an accidental overwrite of a redacted document is recoverable.
  versioning {
    enabled = true
  }

  # Lifecycle: drop noncurrent versions after a retention window to bound storage.
  lifecycle_rule {
    condition {
      num_newer_versions = 5
    }
    action {
      type = "Delete"
    }
  }

  depends_on = [
    google_project_service.required,
    google_kms_crypto_key_iam_member.storage,
  ]
}

# Raw reviewed inputs have a separate policy boundary. The pipeline can read but never mutate
# this bucket; only the governed bucket above receives registry/ACL artifacts and redacted text.
resource "google_storage_bucket" "raw_sources" {
  name     = "enterprise-knowledge-base-raw-${var.project_id}"
  project  = var.project_id
  location = var.region

  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  encryption {
    default_kms_key_name = google_kms_crypto_key.kb.id
  }

  versioning {
    enabled = true
  }

  lifecycle_rule {
    condition {
      num_newer_versions = 5
    }
    action {
      type = "Delete"
    }
  }

  depends_on = [
    google_project_service.required,
    google_kms_crypto_key_iam_member.storage,
  ]
}

# Reviewed control artifacts are isolated from pipeline writes. A separate publisher owns
# registry/ACL updates; refresh code receives read-only access to this versioned authority.
resource "google_storage_bucket" "control_inputs" {
  name     = "enterprise-knowledge-base-control-${var.project_id}"
  project  = var.project_id
  location = var.region

  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  encryption {
    default_kms_key_name = google_kms_crypto_key.kb.id
  }

  versioning {
    enabled = true
  }

  lifecycle_rule {
    condition {
      num_newer_versions = 20
    }
    action {
      type = "Delete"
    }
  }

  depends_on = [
    google_project_service.required,
    google_kms_crypto_key_iam_member.storage,
  ]
}

resource "google_storage_bucket_iam_member" "pipeline_governed_object_admin" {
  bucket = google_storage_bucket.corpus.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.pipeline.email}"
}

resource "google_storage_bucket_iam_member" "pipeline_raw_source_viewer" {
  bucket = google_storage_bucket.raw_sources.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.pipeline.email}"
}

resource "google_storage_bucket_iam_member" "source_publisher" {
  bucket = google_storage_bucket.raw_sources.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${var.source_publisher_service_account_email}"
}

resource "google_storage_bucket_iam_member" "pipeline_control_viewer" {
  bucket = google_storage_bucket.control_inputs.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.pipeline.email}"
}

resource "google_storage_bucket_iam_member" "control_publisher" {
  bucket = google_storage_bucket.control_inputs.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${var.control_publisher_service_account_email}"
}
