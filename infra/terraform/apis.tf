# apis.tf : Enable exactly the managed services A2 depends on.
#
# General Principle map:
#   P-01 (managed-first / minimal surface): only the services the pinned stack (SPEC §3)
#         actually uses are enabled : nothing speculative.
#   P-03 (residency): enabling these APIs is a prerequisite for the regional,
#         CMEK-protected resources defined in the sibling files.
#
# disable_on_destroy = false so a `terraform destroy` of this stack does not yank platform
# APIs out from under other workloads in a shared project.

locals {
  required_services = [
    "aiplatform.googleapis.com",           # regional Vertex AI Gemini inference
    "artifactregistry.googleapis.com",     # immutable regional workload images
    "certificatemanager.googleapis.com",   # regional managed TLS certificate for the SG edge
    "storage.googleapis.com",              # Cloud Storage : single-region corpus bucket
    "dlp.googleapis.com",                  # Sensitive Data Protection / DLP (PII redaction)
    "modelarmor.googleapis.com",           # Model Armor guardrail
    "logging.googleapis.com",              # Cloud Logging (WORM locked bucket + audit)
    "cloudtrace.googleapis.com",           # Cloud Trace (OpenTelemetry spans)
    "observability.googleapis.com",        # regional CMEK _Trace observability bucket
    "alloydb.googleapis.com",              # AlloyDB freshness ledger + chunk vector store
    "cloudscheduler.googleapis.com",       # Corpus freshness refresh cron
    "run.googleapis.com",                  # Cloud Run job / app host
    "iap.googleapis.com",                  # verified-user edge for the managed API
    "networkconnectivity.googleapis.com",  # regional PSC endpoint for Model Armor
    "dns.googleapis.com",                  # private Model Armor endpoint resolution
    "cloudkms.googleapis.com",             # Regional CMEK key ring (P-09)
    "accesscontextmanager.googleapis.com", # VPC Service Controls perimeter (P-03)
    # Supporting services the above transitively require.
    "servicenetworking.googleapis.com", # Private Service Access for AlloyDB
    "compute.googleapis.com",           # VPC / PSA range
    "iam.googleapis.com",               # Service accounts / least-privilege IAM
    "orgpolicy.googleapis.com",         # Org Policy residency constraints (P-03)
  ]
}

resource "google_project_service" "required" {
  for_each = toset(local.required_services)

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}
