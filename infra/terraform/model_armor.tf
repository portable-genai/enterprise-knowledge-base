# Regional Model Armor template used by the managed guardrail adapter. Enforcement blocks rather
# than merely inspecting; partial detector failures are not ignored.
resource "google_model_armor_template" "kb" {
  project     = var.project_id
  location    = var.region
  template_id = "enterprise-knowledge-base-guardrail"

  filter_config {
    pi_and_jailbreak_filter_settings {
      filter_enforcement = "ENABLED"
      confidence_level   = "MEDIUM_AND_ABOVE"
    }
    sdp_settings {
      basic_config {
        filter_enforcement = "ENABLED"
      }
    }
    rai_settings {
      dynamic "rai_filters" {
        for_each = toset(["SEXUALLY_EXPLICIT", "HATE_SPEECH", "HARASSMENT", "DANGEROUS"])
        content {
          filter_type      = rai_filters.value
          confidence_level = "MEDIUM_AND_ABOVE"
        }
      }
    }
  }

  template_metadata {
    enforcement_type                   = "INSPECT_AND_BLOCK"
    ignore_partial_invocation_failures = false
  }

  depends_on = [google_project_service.required]
}

output "model_armor_template" {
  description = "Regional Model Armor template id used by settings.model_armor.template_id."
  value       = google_model_armor_template.kb.template_id
}
