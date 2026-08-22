# kms.tf : Regional Customer-Managed Encryption Keys (CMEK) in Singapore.
#
# General Principle map:
#   P-09 (CMEK does NOT cascade): a CMEK on one resource does not automatically protect
#         data that resource hands to another service. Each managed service (AlloyDB,
#         AlloyDB, Cloud Storage, Cloud Run and Logging outputs) must be told to use this key
#         explicitly. We keep ONE regional key ring
#         + crypto key here and wire it into every CMEK-capable resource in its own file.
#   P-03 (residency): the key ring location is asia-southeast1 : a regional key, never the
#         global/multi-region key. Regional CMEK is what pins crypto material in-country.

resource "google_kms_key_ring" "kb" {
  name     = "enterprise-knowledge-base-ring"
  location = var.region # asia-southeast1 : regional, in-country key material (P-03)

  depends_on = [google_project_service.required]
}

resource "google_kms_crypto_key" "kb" {
  name     = "enterprise-knowledge-base-cmek"
  key_ring = google_kms_key_ring.kb.id

  purpose         = "ENCRYPT_DECRYPT"
  rotation_period = "7776000s" # 90 days : periodic rotation for key hygiene

  version_template {
    algorithm        = "GOOGLE_SYMMETRIC_ENCRYPTION"
    protection_level = "SOFTWARE"
  }

  lifecycle {
    # A destroyed key is unrecoverable and would strand all CMEK-encrypted data.
    prevent_destroy = true
  }
}

# --------------------------------------------------------------------------- #
# Grant each service agent the right to use the key. CMEK does not cascade (P-09):
# every service that encrypts with this key needs its OWN binding here.
# --------------------------------------------------------------------------- #
data "google_project" "this" {
  project_id = var.project_id
}

# Materialize Artifact Registry's service agent before granting repository CMEK access. Enabling
# the API alone may create it asynchronously and race the first repository on a fresh project.
resource "google_project_service_identity" "artifact_registry" {
  provider = google-beta
  project  = var.project_id
  service  = "artifactregistry.googleapis.com"

  depends_on = [google_project_service.required]
}

# Fresh projects do not necessarily materialize service agents synchronously when an API is
# enabled. Resolve each CMEK principal explicitly before creating a binding or encrypted resource.
resource "google_project_service_identity" "alloydb" {
  provider = google-beta
  project  = var.project_id
  service  = "alloydb.googleapis.com"

  depends_on = [google_project_service.required]
}

resource "google_project_service_identity" "cloud_run" {
  provider = google-beta
  project  = var.project_id
  service  = "run.googleapis.com"

  depends_on = [google_project_service.required]
}

resource "google_project_service_identity" "observability" {
  provider = google-beta
  project  = var.project_id
  service  = "observability.googleapis.com"

  depends_on = [google_project_service.required]
}

data "google_storage_project_service_account" "storage" {
  project = var.project_id

  depends_on = [google_project_service.required]
}

# Cloud Logging owns a dedicated project CMEK identity exposed by its Settings API. It can differ
# from the generic service identity (notably on legacy projects), so the documented settings
# contract is the only correct principal for log-bucket encryption.
data "google_logging_project_cmek_settings" "logging" {
  project = var.project_id

  depends_on = [google_project_service.required]
}

resource "terraform_data" "logging_cmek_identity" {
  input = data.google_logging_project_cmek_settings.logging.service_account_id

  lifecycle {
    precondition {
      condition = (
        !var.enable_vpc_sc ||
        !startswith(data.google_logging_project_cmek_settings.logging.service_account_id, "cmek-p")
      )
      error_message = "VPC-SC cannot use a legacy cmek-p Cloud Logging CMEK identity. Migrate the project's Logging CMEK service account to the Settings API loggingServiceAccountId before applying this perimeter."
    }
  }
}

resource "google_kms_crypto_key_iam_member" "artifact_registry" {
  crypto_key_id = google_kms_crypto_key.kb.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:${google_project_service_identity.artifact_registry.email}"
}

# AlloyDB service agent.
resource "google_kms_crypto_key_iam_member" "alloydb" {
  crypto_key_id = google_kms_crypto_key.kb.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:${google_project_service_identity.alloydb.email}"
}

# Cloud Storage service agent (CMEK on the corpus bucket).
resource "google_kms_crypto_key_iam_member" "storage" {
  crypto_key_id = google_kms_crypto_key.kb.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:${data.google_storage_project_service_account.storage.email_address}"
}

# Cloud Logging service agent (CMEK on the WORM bucket).
resource "google_kms_crypto_key_iam_member" "logging" {
  crypto_key_id = google_kms_crypto_key.kb.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:${data.google_logging_project_cmek_settings.logging.service_account_id}"

  depends_on = [terraform_data.logging_cmek_identity]
}

# Cloud Run service agent encrypts API revisions with the regional key.
resource "google_kms_crypto_key_iam_member" "cloud_run" {
  crypto_key_id = google_kms_crypto_key.kb.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:${google_project_service_identity.cloud_run.email}"
}

# The system-created _Trace bucket uses the Observability service identity, not the Trace writer.
resource "google_kms_crypto_key_iam_member" "observability" {
  crypto_key_id = google_kms_crypto_key.kb.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:${google_project_service_identity.observability.email}"
}
