# providers.tf : Provider pinning for the A2 Enterprise Knowledge Base sovereign deploy.
#
# General Principle map:
#   P-03 (data residency / in-country): every provider call is pinned to the Singapore
#         region asia-southeast1. There is no global/multi-region default.
#   P-02 (no lock-in): Terraform is the only place infra is described; the app itself
#         talks to ports, not these resources.
#
# google-beta is required because several sovereignty resources (Model Armor templates,
# Assured Workloads, some Access Context Manager fields) are only exposed on the beta
# surface as of the pinned provider line.

terraform {
  required_version = ">= 1.9.0"

  # The release workflow supplies the reviewed Singapore GCS bucket and per-project prefix.
  # The bucket is an organization-foundation prerequisite because Terraform cannot safely
  # create the backend that must durably coordinate its own multi-job bootstrap/apply state.
  backend "gcs" {}

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0" # 6.x line : current GA surface (mid-2026)
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 6.0"
    }
    external = {
      source  = "hashicorp/external"
      version = "~> 2.3"
    }
  }
}

# Primary (GA) provider : every resource defaults to Singapore.
provider "google" {
  project = var.project_id
  region  = var.region # asia-southeast1 (Singapore) : pinned, never global
}

# Beta provider : same project/region, used only where a resource needs it.
provider "google-beta" {
  project = var.project_id
  region  = var.region
}
