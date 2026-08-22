# The managed API/UI/pipeline profile is complete. Agent Runtime is a separate optional future
# surface and is not deployed by this stack or by the release workflow. This retained toggle is a
# fail-closed guard against an out-of-band SDK deployment acquiring unreviewed authority.
locals {
  managed_profile_implemented = true
  # No Agent Runtime SDK transport currently supplies immutable verified invocation
  # actor/tenant/ACL metadata. Deployment stays disabled until such a bridge is proven.
  managed_agent_context_bridge_implemented = false
}

variable "managed_runtime_deploy_enabled" {
  description = "Future Agent Runtime opt-in; deliberately blocked until verified invocation context exists."
  type        = bool
  default     = false
}

check "vpc_sc_requires_access_policy" {
  assert {
    condition     = !var.enable_vpc_sc || length(trimspace(var.access_policy_id)) > 0
    error_message = "enable_vpc_sc=true requires a reviewed non-empty access_policy_id."
  }
}

check "production_mode_requires_enforced_controls" {
  assert {
    condition = (
      !var.production_mode ||
      (
        var.lock_worm_bucket &&
        var.enable_vpc_sc &&
        !var.vpc_sc_dry_run &&
        var.gemini_single_zone_pt_confirmed
      )
    )
    error_message = "production_mode requires the explicit WORM lock, enforcing VPC-SC (not dry-run), and a reviewed Singapore Gemini Provisioned Throughput order."
  }
}

# `check` blocks expose friendly diagnostics, while these resource preconditions make the same
# readiness contract plan/apply-blocking rather than advisory.
resource "terraform_data" "production_readiness" {
  input = {
    production_mode = var.production_mode
  }

  lifecycle {
    precondition {
      condition     = !var.enable_vpc_sc || length(trimspace(var.access_policy_id)) > 0
      error_message = "enable_vpc_sc=true requires a reviewed non-empty access_policy_id."
    }
    precondition {
      condition = (
        !var.production_mode ||
        (
          var.lock_worm_bucket &&
          var.enable_vpc_sc &&
          !var.vpc_sc_dry_run &&
          var.gemini_single_zone_pt_confirmed
        )
      )
      error_message = "production_mode requires the explicit WORM lock, enforcing VPC-SC (not dry-run), and a reviewed Singapore Gemini Provisioned Throughput order."
    }
  }
}

check "managed_profile_is_implemented_before_serving" {
  assert {
    condition = (
      !var.managed_runtime_deploy_enabled ||
      (local.managed_profile_implemented && local.managed_agent_context_bridge_implemented)
    )
    error_message = "managed_runtime_deploy_enabled is blocked until a trusted Agent Runtime invocation-context bridge supplies verified actor, tenant and principals."
  }
}
