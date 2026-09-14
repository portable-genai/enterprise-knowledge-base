# Cloud Trace now persists spans in a system-created Observability `_Trace` bucket. The pinned
# Google provider doesn't yet manage Observability default settings/buckets, so a read-only
# external check makes the effective API state part of every full plan/apply. Platform foundation
# owners configure the defaults after phase-1 creates the regional key and before approving phase 2.
data "external" "observability_foundation" {
  program = ["bash", "${path.module}/../../scripts/check_managed_observability_foundation.sh"]

  query = {
    project_id   = var.project_id
    region       = var.region
    kms_key_name = one(google_kms_crypto_key.kb[*].id)
  }

  depends_on = [
    google_project_service.required,
    google_kms_crypto_key_iam_member.observability,
  ]
}

resource "terraform_data" "observability_foundation" {
  input = data.external.observability_foundation.result

  lifecycle {
    precondition {
      condition     = data.external.observability_foundation.result.default_storage_location == var.region
      error_message = "Observability default storage must be var.region before Cloud Trace can be enabled."
    }
    precondition {
      # Reconciled only when this stack holds a key. With cmek_enabled false there is no
      # reviewed key for the project's observability defaults to match, and the default
      # Google-managed encryption is the stated posture rather than a drift.
      condition     = !var.cmek_enabled || data.external.observability_foundation.result.default_kms_key == one(google_kms_crypto_key.kb[*].id)
      error_message = "Observability defaults must use the reviewed regional CMEK while cmek_enabled is true."
    }
    precondition {
      condition = (
        data.external.observability_foundation.result.trace_bucket_count == "0" ||
        (
          data.external.observability_foundation.result.trace_bucket_count == "1" &&
          data.external.observability_foundation.result.trace_bucket_location == var.region &&
          data.external.observability_foundation.result.trace_bucket_kms_key == one(google_kms_crypto_key.kb[*].id)
        )
      )
      error_message = "An existing _Trace bucket must be the singleton in-region bucket encrypted by the reviewed CMEK."
    }
    precondition {
      condition = (
        !var.production_mode ||
        data.external.observability_foundation.result.trace_bucket_count == "1"
      )
      error_message = "production_mode requires effective proof of the initialized in-region CMEK _Trace bucket."
    }
  }
}
