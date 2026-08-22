# One regional build-output boundary for all managed workloads. Terraform consumes only
# immutable digests from this repository; image construction/push is a separate reviewed CI step.
resource "google_artifact_registry_repository" "images" {
  project       = var.project_id
  location      = var.region
  repository_id = "enterprise-knowledge-base"
  description   = "Immutable Enterprise KB API, UI and refresh images"
  format        = "DOCKER"
  kms_key_name  = google_kms_crypto_key.kb.id

  cleanup_policy_dry_run = true

  depends_on = [
    google_project_service.required,
    google_kms_crypto_key_iam_member.artifact_registry,
    # Phase-1 targets this repository. Pull the Observability service identity/key grant into the
    # same foundation phase so platform owners can set _Trace defaults before phase-2 approval.
    google_kms_crypto_key_iam_member.observability,
  ]
}

resource "google_artifact_registry_repository_iam_member" "publisher" {
  project    = var.project_id
  location   = google_artifact_registry_repository.images.location
  repository = google_artifact_registry_repository.images.name
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${var.artifact_publisher_service_account_email}"
}

locals {
  artifact_repo_prefix = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}/"
}
