# variables.tf : The only knobs. Everything else is a concrete in-region value.
#
# General Principle map:
#   P-03 (residency): `region` is SELECTED AT DEPLOY TIME and validated at PLAN time
#         against `allowed_regions`, the residency allowlist, so a caller fails fast rather
#         than deploying to an unvetted, out-of-jurisdiction region. The default stays
#         asia-southeast1; deploying elsewhere means setting BOTH variables, which is the
#         deliberate residency review. The runtime remains region-parametric.
#   P-08 (auditability/retention): `retention_days` is a Terraform variable (the WORM
#         bucket lock is irreversible, so retention must be deliberate).
#
# Per the build contract, ONLY project_id and a couple of genuinely per-tenant values
# (org/billing ids and the VPC-SC toggle) are variables. All
# service identifiers, locations, and template names are concrete.

variable "project_id" {
  description = "Target GCP project id (required). Single-tenant, Singapore-resident."
  type        = string
}

variable "allowed_regions" {
  description = <<-EOT
    Residency allowlist: the regions this deployment may create resources in (P-03).
    Drives BOTH the region validation below and the gcp.resourceLocations Org Policy, so
    the allowlist cannot be enforced in one place and forgotten in the other. Extending this
    list is the deliberate residency review point: confirm the regional service, capacity and
    obligation evidence for every dependency in that region first. Mirror any change in
    config/settings.yaml `residency.allowed_regions`.
  EOT
  type        = list(string)
  default     = ["asia-southeast1"]

  validation {
    condition     = length(var.allowed_regions) > 0
    error_message = "allowed_regions must list at least one residency-approved region."
  }
}

variable "region" {
  description = <<-EOT
    Deployment region, SELECTED AT DEPLOY TIME. Keeps the Singapore default below but is
    overridable. Validated against var.allowed_regions so an unapproved region fails fast at
    `terraform plan` rather than deploying data out of jurisdiction (P-03).
  EOT
  type        = string
  default     = "asia-southeast1"

  validation {
    condition     = contains(var.allowed_regions, var.region)
    error_message = "region must be one of var.allowed_regions (residency allowlist). Add it there first if that region is approved for this workload (P-03)."
  }
}

variable "zone" {
  description = "Default zone within Singapore for zonal resources."
  type        = string
  default     = "asia-southeast1-a"
}

variable "retention_days" {
  description = "WORM audit-log retention in days. Default ~7 years. Lock is irreversible."
  type        = number
  default     = 2557 # ~7 years; mirrors config/settings.yaml logging.retention_days

  validation {
    condition     = var.retention_days >= 2557
    error_message = "Governance retention must be at least 2557 days (~7 years) (P-08)."
  }
}

variable "production_mode" {
  description = "Require irreversible production controls rather than the recoverable demo posture."
  type        = bool
  default     = false
}

variable "lock_worm_bucket" {
  description = "Irreversibly lock the audit bucket for retention_days. Explicitly confirm only for production."
  type        = bool
  default     = false
}

variable "org_id" {
  description = "Organization id : required for Org Policy and Access Context Manager."
  type        = string
}

variable "billing_account" {
  description = "Billing account id (used by Assured Workloads / FinOps tagging)."
  type        = string
  default     = ""
}

variable "access_policy_id" {
  description = <<-EOT
    Existing Access Context Manager policy id (numeric, no prefix) for the org.
    Required when enable_vpc_sc = true; the service perimeter is created under it.
    Create once per org with:
      gcloud access-context-manager policies create \
        --organization=ORG_ID --title="sg-residency"
  EOT
  type        = string
  default     = ""
}

variable "vpc_network_name" {
  description = "Name of the VPC that hosts the private AlloyDB instance and PSA range."
  type        = string
  default     = "enterprise-knowledge-base-vpc"
}

variable "serverless_subnet_cidr" {
  description = "Reviewed RFC1918 CIDR for Direct VPC egress; Cloud Run requires /26 or larger."
  type        = string
  default     = "10.42.0.0/26"

  validation {
    condition = (
      can(cidrnetmask(var.serverless_subnet_cidr)) &&
      can(tonumber(split("/", var.serverless_subnet_cidr)[1])) &&
      tonumber(split("/", var.serverless_subnet_cidr)[1]) <= 26
    )
    error_message = "serverless_subnet_cidr must be a valid CIDR with a /26 or larger address range."
  }
}

variable "lb_proxy_subnet_cidr" {
  description = "Reviewed non-overlapping /23 or larger CIDR for the regional external ALB proxy-only subnet."
  type        = string
  default     = "10.42.2.0/23"

  validation {
    condition = (
      can(cidrnetmask(var.lb_proxy_subnet_cidr)) &&
      can(tonumber(split("/", var.lb_proxy_subnet_cidr)[1])) &&
      tonumber(split("/", var.lb_proxy_subnet_cidr)[1]) <= 23
    )
    error_message = "lb_proxy_subnet_cidr must be a valid non-overlapping CIDR with a /23 or larger range."
  }
}

variable "refresh_job_image" {
  description = "Immutable, reviewed Artifact Registry refresh image including its sha256 digest."
  type        = string
  default     = ""

  validation {
    condition     = var.bootstrap_only || can(regex("^[^[:space:]]+@sha256:[0-9a-f]{64}$", var.refresh_job_image))
    error_message = "refresh_job_image must be immutable and end in @sha256:<64 lowercase hex>."
  }
}

variable "artifact_publisher_service_account_email" {
  description = "Reviewed WIF-authenticated CI service account granted writer only on this image repository."
  type        = string

  validation {
    condition     = can(regex("^[^[:space:]]+@[^[:space:]]+\\.iam\\.gserviceaccount\\.com$", var.artifact_publisher_service_account_email))
    error_message = "artifact_publisher_service_account_email must be an explicit service-account email."
  }
}

variable "control_publisher_service_account_email" {
  description = "Reviewed publisher of versioned registry and ACL authority objects; pipeline is read-only."
  type        = string

  validation {
    condition     = can(regex("^[^[:space:]]+@[^[:space:]]+\\.iam\\.gserviceaccount\\.com$", var.control_publisher_service_account_email))
    error_message = "control_publisher_service_account_email must be an explicit service-account email."
  }
}

variable "source_publisher_service_account_email" {
  description = "Reviewed publisher of raw source objects; pipeline is read-only."
  type        = string

  validation {
    condition     = can(regex("^[^[:space:]]+@[^[:space:]]+\\.iam\\.gserviceaccount\\.com$", var.source_publisher_service_account_email))
    error_message = "source_publisher_service_account_email must be an explicit service-account email."
  }
}

variable "app_image" {
  description = "Immutable, reviewed regional Artifact Registry image for the FastAPI service."
  type        = string
  default     = ""

  validation {
    condition     = var.bootstrap_only || can(regex("^[^[:space:]]+@sha256:[0-9a-f]{64}$", var.app_image))
    error_message = "app_image must be immutable and end in @sha256:<64 lowercase hex>."
  }
}

variable "ui_image" {
  description = "Immutable, reviewed regional Artifact Registry image for the Next.js UI."
  type        = string
  default     = ""

  validation {
    condition     = var.bootstrap_only || can(regex("^[^[:space:]]+@sha256:[0-9a-f]{64}$", var.ui_image))
    error_message = "ui_image must be immutable and end in @sha256:<64 lowercase hex>."
  }
}

variable "bootstrap_only" {
  description = "Allow empty image inputs only for the documented targeted API/KMS/Artifact Registry bootstrap phase. Never use for a full apply."
  type        = bool
  default     = false
}

variable "api_domain" {
  description = "Reviewed DNS name routed to the IAP-protected managed API HTTPS load balancer."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])$", var.api_domain))
    error_message = "api_domain must be a lowercase DNS hostname without a scheme or path."
  }
}

variable "public_dns_managed_zone" {
  description = "Existing public Cloud DNS managed-zone name authoritative for api_domain."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,62}$", var.public_dns_managed_zone))
    error_message = "public_dns_managed_zone must be an explicit Cloud DNS managed-zone name."
  }
}

variable "iap_accessors" {
  description = "Reviewed users, groups and service accounts allowed through IAP."
  type        = set(string)

  validation {
    condition = (
      length(var.iap_accessors) > 0 &&
      alltrue([for member in var.iap_accessors : can(regex("^(user|group|serviceAccount):[^[:space:]]+@[^[:space:]]+$", member))])
    )
    error_message = "iap_accessors must contain explicit user:, group: or serviceAccount: members."
  }
}

variable "iap_backend_service_id" {
  description = "Generated regional API backend numeric id; bootstrap-pending is fail-closed and permitted only for the first edge-creation apply."
  type        = string
  default     = "bootstrap-pending"

  validation {
    condition     = var.iap_backend_service_id == "bootstrap-pending" || can(regex("^[0-9]+$", var.iap_backend_service_id))
    error_message = "iap_backend_service_id must be the generated numeric backend id or bootstrap-pending for the first fail-closed edge apply."
  }
}

variable "s2s_allowed_callers" {
  description = "Reviewed service-account emails permitted to call governed API routes."
  type        = set(string)

  validation {
    condition = (
      length(var.s2s_allowed_callers) > 0 &&
      alltrue([for caller in var.s2s_allowed_callers : can(regex("^[^[:space:]]+@[^[:space:]]+\\.iam\\.gserviceaccount\\.com$", caller))])
    )
    error_message = "s2s_allowed_callers must contain explicit service-account emails."
  }
}

variable "s2s_service_tenants" {
  description = "Reviewed tenant partition for every verified IAP service-account principal."
  type        = map(string)

  validation {
    condition = alltrue([
      for email, tenant in var.s2s_service_tenants :
      can(regex("^[^[:space:]]+@[^[:space:]]+\\.iam\\.gserviceaccount\\.com$", email)) && length(trimspace(tenant)) > 0
    ])
    error_message = "s2s_service_tenants must map service-account emails to non-empty tenant identifiers."
  }
}

check "s2s_iap_identity_is_fully_reviewed" {
  assert {
    condition = (
      toset(keys(var.s2s_service_tenants)) == var.s2s_allowed_callers &&
      alltrue([for email in var.s2s_allowed_callers : contains(var.iap_accessors, "serviceAccount:${email}")])
    )
    error_message = "each s2s_allowed_callers identity needs an exact serviceAccount: IAP accessor and tenant mapping."
  }
}

variable "gemini_single_zone_pt_confirmed" {
  description = "Release-owner confirmation that a reviewed Singapore single-zone Gemini 3.5 Flash Provisioned Throughput order is active."
  type        = bool
  default     = false
}

variable "corpus_registry_uri" {
  description = "Reviewed YAML registry under the Terraform-managed control-input bucket registry/ prefix."
  type        = string

  validation {
    condition     = can(regex("^gs://[a-z0-9._-]+/.+\\.ya?ml$", var.corpus_registry_uri))
    error_message = "corpus_registry_uri must name a reviewed gs://BUCKET/OBJECT.yaml resource."
  }
}

variable "acl_bindings_uri" {
  description = "Reviewed ACL binding JSON under the Terraform-managed control-input bucket acl/."
  type        = string

  validation {
    condition     = can(regex("^gs://[a-z0-9._-]+/acl/.+\\.json$", var.acl_bindings_uri))
    error_message = "acl_bindings_uri must name a reviewed gs://BUCKET/acl/OBJECT.json resource."
  }
}

variable "enable_vpc_sc" {
  description = "Create the VPC Service Controls perimeter around the AI/data APIs (P-03)."
  type        = bool
  default     = true
}

variable "vpc_sc_dry_run" {
  description = <<-EOT
    Create the perimeter in DRY-RUN mode first (the default, and the only safe first
    apply). A dry-run perimeter logs what it WOULD block without denying anything, so an
    operator sees the real call graph before enforcement can strand the deployment or the
    CI identity. Flip to false only after the dry-run audit log is clean; enforcement is
    then a one-line tfvars change, not a code change.
  EOT
  type        = bool
  default     = true
}
