# org_policy.tf : Org Policy constraints enforcing Singapore residency.
#
# General Principle map:
#   P-03 (data residency, defence in depth): even if someone hand-edits a resource,
#         these org policies REJECT the creation of resources outside Singapore.
#         gcp.resourceLocations is the master residency control; the rest harden
#         the project (no public IPs on VMs, uniform bucket access, restrict
#         external IPs) so data and compute stay in-country and private (P-05).
#
# Scoped to the project via google_project. To enforce org-wide, move these to an
# org-level google_org_policy_policy with parent = "organizations/${var.org_id}".
# verify: https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/org_policy_policy

# Master residency policy: allow ONLY the regions on the residency allowlist. The values
# are generated from var.allowed_regions, so the Org Policy and the plan-time region
# validation cannot disagree. This reference module additionally validates Singapore exactly;
# another region needs its own reviewed infrastructure/capacity evidence while core code stays
# region-parametric.
resource "google_org_policy_policy" "resource_locations" {
  name   = "projects/${var.project_id}/policies/gcp.resourceLocations"
  parent = "projects/${var.project_id}"

  spec {
    rules {
      values {
        # e.g. in:asia-southeast1-locations confines resources to the Singapore region.
        allowed_values = [for r in var.allowed_regions : "in:${r}-locations"]
      }
    }
  }

  depends_on = [google_project_service.required]
}

# Disable VM external IPs : keep the data plane private (P-05).
resource "google_org_policy_policy" "no_external_ip" {
  name   = "projects/${var.project_id}/policies/compute.vmExternalIpAccess"
  parent = "projects/${var.project_id}"

  spec {
    rules {
      deny_all = "TRUE"
    }
  }

  depends_on = [google_project_service.required]
}

# Require uniform bucket-level access (no per-object ACL exfiltration paths).
resource "google_org_policy_policy" "uniform_bucket_access" {
  name   = "projects/${var.project_id}/policies/storage.uniformBucketLevelAccess"
  parent = "projects/${var.project_id}"

  spec {
    rules {
      enforce = "TRUE"
    }
  }

  depends_on = [google_project_service.required]
}

# Workload Identity Federation and short-lived IAM credentials are the only supported automation
# path. Long-lived service-account keys bypass that review boundary and are disabled both ways:
# neither new keys nor uploaded external public keys are permitted in this project.
resource "google_org_policy_policy" "disable_service_account_key_creation" {
  name   = "projects/${var.project_id}/policies/iam.disableServiceAccountKeyCreation"
  parent = "projects/${var.project_id}"

  spec {
    rules {
      enforce = "TRUE"
    }
  }

  depends_on = [google_project_service.required]
}

resource "google_org_policy_policy" "disable_service_account_key_upload" {
  name   = "projects/${var.project_id}/policies/iam.disableServiceAccountKeyUpload"
  parent = "projects/${var.project_id}"

  spec {
    rules {
      enforce = "TRUE"
    }
  }

  depends_on = [google_project_service.required]
}

# Restrict which CMEK projects can be used : keep crypto in this project/region.
resource "google_org_policy_policy" "restrict_cmek_projects" {
  name   = "projects/${var.project_id}/policies/gcp.restrictNonCmekServices"
  parent = "projects/${var.project_id}"

  spec {
    rules {
      # Require CMEK for the data-bearing services (no Google-managed-key fallback).
      values {
        denied_values = [
          "alloydb.googleapis.com",
          "logging.googleapis.com",
          "observability.googleapis.com",
        ]
      }
    }
  }

  depends_on = [google_project_service.required]
}
