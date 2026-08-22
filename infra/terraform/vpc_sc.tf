# vpc_sc.tf : VPC Service Controls perimeter around the AI/data plane.
#
# General Principle map:
#   P-03 (residency + exfiltration control): a service perimeter draws a logical boundary
#         around sovereignty-critical APIs (Vertex, Storage, DLP, Model Armor, Logging,
#         AlloyDB and KMS). Data cannot be
#         read across the boundary to a non-Singapore project, which is what stops the
#         corpus and audit log from leaving the country.
#   P-01 (least surface): only the services A2 uses are inside the perimeter.
#
# Guarded by var.enable_vpc_sc so non-prod/dev applies can skip it (count = 0), and by
# var.vpc_sc_dry_run so the FIRST apply is always dry-run: the perimeter is created with
# an explicit dry-run spec that logs what it would block and denies nothing. Enforcement
# is a tfvars flip once the dry-run audit log is clean.
#
# DEPLOY-ORDER CAVEAT:
#   The managed release creates the enabled perimeter in DRY-RUN mode on its first full apply,
#   after the API/KMS/repository bootstrap. Review the resulting violations and provision any
#   required access level before changing `vpc_sc_dry_run` to false. Production readiness refuses
#   a disabled perimeter and refuses production while it is still dry-run.

locals {
  perimeter_restricted_services = [
    "aiplatform.googleapis.com",
    "storage.googleapis.com",
    "dlp.googleapis.com",
    "modelarmor.googleapis.com",
    "logging.googleapis.com",
    "cloudtrace.googleapis.com",
    "observability.googleapis.com",
    "alloydb.googleapis.com",
    "cloudkms.googleapis.com",
  ]
}

resource "google_access_context_manager_service_perimeter" "kb" {
  count = var.enable_vpc_sc ? 1 : 0

  parent = "accessPolicies/${var.access_policy_id}"
  name   = "accessPolicies/${var.access_policy_id}/servicePerimeters/enterprise_kb_sg"
  title  = "enterprise_kb_sg"

  perimeter_type = "PERIMETER_TYPE_REGULAR"

  # Dry-run first (the default). use_explicit_dry_run_spec makes `spec` the dry-run
  # configuration and leaves enforcement empty, so violations are LOGGED, not denied.
  use_explicit_dry_run_spec = var.vpc_sc_dry_run

  dynamic "spec" {
    for_each = var.vpc_sc_dry_run ? [1] : []
    content {
      resources           = ["projects/${data.google_project.this.number}"]
      restricted_services = local.perimeter_restricted_services

      vpc_accessible_services {
        enable_restriction = true
        allowed_services   = local.perimeter_restricted_services
      }
    }
  }

  # Enforcing configuration, applied only once the dry-run audit is clean.
  dynamic "status" {
    for_each = var.vpc_sc_dry_run ? [] : [1]
    content {
      # Confine the project's sovereignty-critical APIs to this perimeter.
      resources           = ["projects/${data.google_project.this.number}"]
      restricted_services = local.perimeter_restricted_services

      # Allow VPC-internal use of every restricted API from inside the boundary.
      vpc_accessible_services {
        enable_restriction = true
        allowed_services   = local.perimeter_restricted_services
      }
    }
  }

  depends_on = [google_project_service.required]
}
