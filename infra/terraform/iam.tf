# iam.tf : Least-privilege service accounts for the A2 workloads.
#
# General Principle map:
#   P-06 (least privilege / separation of duties): distinct identities : the app
#         (serving), pipeline (ingestion/freshness), and migration. The disabled Agent Runtime
#         gets no identity. Each active workload gets only the roles it needs; no shared
#         "kitchen-sink" SA.
#   P-03 (residency): identities are project-scoped; data access is to in-region services.
#   P-09 (CMEK + ACL): managed service agents, not workload identities, perform envelope
#         encryption. The app can read tenant-scoped principal-to-tag bindings from AlloyDB.

# ------------------------------- App (serving) ------------------------------ #
resource "google_service_account" "app" {
  account_id   = "enterprise-kb-app"
  display_name = "A2 Enterprise KB app (serving / API)"
  project      = var.project_id

  depends_on = [google_project_service.required]
}

locals {
  # Serving path: query corpus (read), resolve ACL tags, call models + guardrail + DLP,
  # write audit + traces, and read the ledger. No write to the corpus (pipeline).
  app_roles = [
    "roles/aiplatform.user",
    "roles/modelarmor.user",                   # screen serving prompts and responses
    "roles/modelarmor.viewer",                 # resolve/read the reviewed sanitize template
    "roles/dlp.user",                          # deidentifyContent at the serve boundary (P-04)
    "roles/logging.logWriter",                 # write redacted audit events to WORM sink
    "roles/cloudtrace.agent",                  # OpenTelemetry spans (content OFF)
    "roles/alloydb.client",                    # read freshness ledger / chunks (PRIVATE)
    "roles/alloydb.databaseUser",              # IAM database login; SQL migration limits table access
    "roles/serviceusage.serviceUsageConsumer", # connector token exchange against project APIs
  ]
}

resource "google_project_iam_member" "app" {
  for_each = toset(local.app_roles)
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.app.email}"
}

# ------------------------- Pipeline (ingestion job) ------------------------- #
resource "google_service_account" "pipeline" {
  account_id   = "enterprise-kb-pipeline"
  display_name = "A2 corpus ingestion / freshness pipeline"
  project      = var.project_id

  depends_on = [google_project_service.required]
}

locals {
  # Ingestion path: portable parse in memory, WRITE redacted text to the corpus bucket,
  # WRITE searchable chunks + freshness to AlloyDB, and log.
  pipeline_roles = [
    "roles/modelarmor.user",                   # screen corpus content before indexing
    "roles/modelarmor.viewer",                 # resolve/read the reviewed sanitize template
    "roles/alloydb.client",                    # upsert the freshness ledger (PRIVATE)
    "roles/alloydb.databaseUser",              # IAM database login; SQL migration grants scoped DML
    "roles/serviceusage.serviceUsageConsumer", # connector token exchange against project APIs
    "roles/dlp.user",                          # redact fetched docs before indexing (P-04)
    "roles/logging.logWriter",
    "roles/cloudtrace.agent",
  ]
}

resource "google_project_iam_member" "pipeline" {
  for_each = toset(local.pipeline_roles)
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.pipeline.email}"
}

# ---------------------- Schema migration (admin, ephemeral) ---------------- #
# This identity is the only database administrator. It has no serving, model, storage, logging
# or corpus role and is used only by the reviewed 000/001 migration workflow.
resource "google_service_account" "migration" {
  account_id   = "enterprise-kb-migration"
  display_name = "A2 AlloyDB schema migration"
  project      = var.project_id

  depends_on = [google_project_service.required]
}

locals {
  migration_roles = [
    "roles/alloydb.client",
    "roles/alloydb.databaseUser",
    "roles/serviceusage.serviceUsageConsumer",
  ]
}

resource "google_project_iam_member" "migration" {
  for_each = toset(local.migration_roles)
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.migration.email}"
}

# Direct VPC egress attaches interfaces as the materialized Cloud Run service agent, not the
# workload SA. Never synthesize this principal from a project number: fresh projects can race
# service-agent creation and future service-agent formats are owned by the API.
data "google_project" "current" {
  project_id = var.project_id
}

resource "google_compute_subnetwork_iam_member" "cloud_run_service_agent" {
  project    = var.project_id
  region     = var.region
  subnetwork = google_compute_subnetwork.serverless.name
  role       = "roles/compute.networkUser"
  member     = "serviceAccount:${google_project_service_identity.cloud_run.email}"

  depends_on = [google_project_service_identity.cloud_run]
}
