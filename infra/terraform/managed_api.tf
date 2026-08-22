# Managed FastAPI serving path: immutable image -> private Cloud Run origin -> IAP HTTPS edge.
# The origin accepts traffic only from internal/load-balancer paths. IAP is the sole invoker
# and injects the signed assertion the application's IdentityPort verifies again.

locals {
  api_service_name = "enterprise-knowledge-base-api"
  # IAP management/IAM is region-scoped for this regional backend, but the current signed-header
  # contract still specifies the Compute backend audience in global-format with the backend's
  # numeric id. Do not substitute a /regions/... resource-management path for the JWT `aud`.
  iap_audience = format(
    "/projects/%s/global/backendServices/%s",
    data.google_project.current.number,
    var.iap_backend_service_id,
  )
  api_environment = {
    KB_PROFILE              = "gcp"
    KB_REGION               = var.region
    GOOGLE_CLOUD_PROJECT    = var.project_id
    KB_KMS_KEY              = google_kms_crypto_key.kb.id
    KB_CORPUS_BUCKET        = google_storage_bucket.corpus.name
    KB_CORPUS_REGISTRY      = var.corpus_registry_uri
    KB_ALLOYDB_URI          = google_alloydb_instance.primary.name
    KB_ALLOYDB_USER         = google_alloydb_user.workload["app"].user_id
    KB_MODEL_ARMOR_TEMPLATE = google_model_armor_template.kb.template_id
    KB_IAP_AUDIENCE         = local.iap_audience
    KB_S2S_AUDIENCE         = "https://${var.api_domain}"
    KB_S2S_ALLOWED_CALLERS  = join(",", sort(tolist(var.s2s_allowed_callers)))
    KB_IAP_SERVICE_TENANTS  = jsonencode(var.s2s_service_tenants)
  }
}

resource "google_compute_region_network_endpoint_group" "api" {
  name                  = "enterprise-knowledge-base-api-neg"
  project               = var.project_id
  region                = var.region
  network_endpoint_type = "SERVERLESS"

  # A literal reviewed service name deliberately avoids a Terraform graph cycle: the
  # backend's generated numeric id is required as the IAP JWT audience in the service.
  cloud_run {
    service = google_cloud_run_v2_service.api.name
  }

  # Google accepts a serverless NEG only after its backing Cloud Run resource exists. The API
  # service itself consumes an explicit backend id, so this order does not create a graph cycle;
  # the release workflow converges that id in its mandatory second apply.
  depends_on = [google_cloud_run_v2_service.api]
}

resource "google_compute_region_backend_service" "api" {
  name                  = "enterprise-knowledge-base-api"
  project               = var.project_id
  region                = var.region
  protocol              = "HTTP"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  network               = google_compute_network.kb.id
  timeout_sec           = 60

  backend {
    group = google_compute_region_network_endpoint_group.api.id
  }

  # Current Google-managed IAP OAuth client: no OAuth secret is accepted into Terraform
  # input or state. Access is still explicit through the IAM members below.
  iap {
    enabled = true
  }

  log_config {
    enable      = true
    sample_rate = 1.0
  }
}

resource "google_compute_region_network_endpoint_group" "ui" {
  name                  = "enterprise-knowledge-base-ui-neg"
  project               = var.project_id
  region                = var.region
  network_endpoint_type = "SERVERLESS"
  cloud_run {
    service = google_cloud_run_v2_service.ui.name
  }
}

resource "google_compute_region_backend_service" "ui" {
  name                  = "enterprise-knowledge-base-ui"
  project               = var.project_id
  region                = var.region
  protocol              = "HTTP"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  network               = google_compute_network.kb.id
  backend {
    group = google_compute_region_network_endpoint_group.ui.id
  }
  iap {
    enabled = true
  }
  log_config {
    enable      = true
    sample_rate = 1.0
  }
}

resource "google_iap_web_region_backend_service_iam_member" "api_accessor" {
  for_each                   = var.iap_accessors
  project                    = var.project_id
  region                     = var.region
  web_region_backend_service = google_compute_region_backend_service.api.name
  role                       = "roles/iap.httpsResourceAccessor"
  member                     = each.value
}

resource "google_iap_web_region_backend_service_iam_member" "ui_accessor" {
  for_each                   = var.iap_accessors
  project                    = var.project_id
  region                     = var.region
  web_region_backend_service = google_compute_region_backend_service.ui.name
  role                       = "roles/iap.httpsResourceAccessor"
  member                     = each.value
}

# Materialize the IAP service identity deterministically before binding it to Cloud Run. Merely
# enabling the API can otherwise race the IAM resources on a fresh project.
resource "google_project_service_identity" "iap" {
  provider = google-beta
  project  = var.project_id
  service  = "iap.googleapis.com"

  depends_on = [google_project_service.required]
}

resource "google_project_iam_member" "iap_service_agent" {
  project = var.project_id
  role    = "roles/iap.serviceAgent"
  member  = "serviceAccount:${google_project_service_identity.iap.email}"
}

resource "google_certificate_manager_dns_authorization" "api" {
  name     = "enterprise-knowledge-base-api"
  project  = var.project_id
  location = var.region
  domain   = var.api_domain
  type     = "PER_PROJECT_RECORD"

  depends_on = [google_project_service.required]
}

resource "google_dns_record_set" "api_certificate_authorization" {
  project      = var.project_id
  managed_zone = var.public_dns_managed_zone
  name         = google_certificate_manager_dns_authorization.api.dns_resource_record[0].name
  type         = google_certificate_manager_dns_authorization.api.dns_resource_record[0].type
  ttl          = 300
  rrdatas      = [google_certificate_manager_dns_authorization.api.dns_resource_record[0].data]
}

resource "google_certificate_manager_certificate" "api" {
  name     = "enterprise-knowledge-base-api"
  project  = var.project_id
  location = var.region
  scope    = "DEFAULT"

  managed {
    domains            = [var.api_domain]
    dns_authorizations = [google_certificate_manager_dns_authorization.api.id]
  }

  depends_on = [google_dns_record_set.api_certificate_authorization]
}

resource "google_compute_region_url_map" "api" {
  name            = "enterprise-knowledge-base-api"
  project         = var.project_id
  region          = var.region
  default_service = google_compute_region_backend_service.ui.id

  host_rule {
    hosts        = [var.api_domain]
    path_matcher = "journey"
  }

  path_matcher {
    name            = "journey"
    default_service = google_compute_region_backend_service.ui.id
    path_rule {
      paths = [
        "/v1",
        "/v1/*",
        "/healthz",
        "/.well-known/*",
        "/openapi.json",
      ]
      service = google_compute_region_backend_service.api.id
    }
  }
}

resource "google_compute_region_target_https_proxy" "api" {
  name                             = "enterprise-knowledge-base-api"
  project                          = var.project_id
  region                           = var.region
  url_map                          = google_compute_region_url_map.api.id
  certificate_manager_certificates = [google_certificate_manager_certificate.api.id]
}

resource "google_compute_address" "api" {
  name         = "enterprise-knowledge-base-api"
  project      = var.project_id
  region       = var.region
  address_type = "EXTERNAL"
  network_tier = "STANDARD"
}

resource "google_dns_record_set" "api" {
  project      = var.project_id
  managed_zone = var.public_dns_managed_zone
  name         = "${var.api_domain}."
  type         = "A"
  ttl          = 300
  rrdatas      = [google_compute_address.api.address]
}

resource "google_compute_forwarding_rule" "api_https" {
  name                  = "enterprise-knowledge-base-api-https"
  project               = var.project_id
  region                = var.region
  ip_address            = google_compute_address.api.id
  port_range            = "443"
  target                = google_compute_region_target_https_proxy.api.id
  load_balancing_scheme = "EXTERNAL_MANAGED"
  network               = google_compute_network.kb.id
  network_tier          = "STANDARD"

  depends_on = [google_compute_subnetwork.lb_proxy_only]
}

resource "google_cloud_run_v2_service" "api" {
  name                = local.api_service_name
  location            = var.region
  project             = var.project_id
  ingress             = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"
  deletion_protection = true

  template {
    service_account = google_service_account.app.email
    encryption_key  = google_kms_crypto_key.kb.id
    timeout         = "60s"

    scaling {
      min_instance_count = 1
      max_instance_count = 20
    }

    containers {
      image = var.app_image

      ports {
        name           = "http1"
        container_port = 8082
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }
        cpu_idle = true
      }

      startup_probe {
        initial_delay_seconds = 2
        timeout_seconds       = 3
        period_seconds        = 5
        failure_threshold     = 12
        http_get {
          path = "/healthz"
          port = 8082
        }
      }

      liveness_probe {
        timeout_seconds   = 3
        period_seconds    = 30
        failure_threshold = 3
        http_get {
          path = "/healthz"
          port = 8082
        }
      }

      dynamic "env" {
        for_each = local.api_environment
        content {
          name  = env.key
          value = env.value
        }
      }
    }

    vpc_access {
      network_interfaces {
        network    = google_compute_network.kb.name
        subnetwork = google_compute_subnetwork.serverless.name
      }
      egress = "ALL_TRAFFIC"
    }
  }

  lifecycle {
    precondition {
      condition = startswith(
        var.app_image,
        local.artifact_repo_prefix
      )
      error_message = "app_image must be an immutable digest from this project's regional Artifact Registry."
    }
    precondition {
      condition     = var.gemini_single_zone_pt_confirmed
      error_message = "Managed API requires a reviewed Singapore single-zone Gemini 3.5 Flash Provisioned Throughput order; Standard PayGo is unsupported."
    }
  }

  depends_on = [
    google_kms_crypto_key_iam_member.cloud_run,
    google_compute_subnetwork_iam_member.cloud_run_service_agent,
    google_project_iam_member.app,
    google_logging_project_bucket_config.default,
    google_logging_project_bucket_config.required,
    terraform_data.observability_foundation,
  ]
}

resource "google_service_account" "ui" {
  account_id   = "enterprise-kb-ui"
  display_name = "A2 Enterprise KB browser UI"
  project      = var.project_id
}

resource "google_project_iam_member" "ui_log_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.ui.email}"
}

resource "google_cloud_run_v2_service" "ui" {
  name                = "enterprise-knowledge-base-ui"
  location            = var.region
  project             = var.project_id
  ingress             = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"
  deletion_protection = true

  template {
    service_account = google_service_account.ui.email
    encryption_key  = google_kms_crypto_key.kb.id
    timeout         = "30s"
    scaling {
      min_instance_count = 1
      max_instance_count = 10
    }
    containers {
      image = var.ui_image
      ports {
        name           = "http1"
        container_port = 3000
      }
      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
        cpu_idle = true
      }
      startup_probe {
        initial_delay_seconds = 2
        timeout_seconds       = 3
        period_seconds        = 5
        failure_threshold     = 12
        http_get {
          path = "/"
          port = 3000
        }
      }
      liveness_probe {
        timeout_seconds   = 3
        period_seconds    = 30
        failure_threshold = 3
        http_get {
          path = "/"
          port = 3000
        }
      }
    }
  }

  lifecycle {
    precondition {
      condition     = startswith(var.ui_image, local.artifact_repo_prefix)
      error_message = "ui_image must be an immutable digest from this project's regional Artifact Registry."
    }
  }
  depends_on = [
    google_kms_crypto_key_iam_member.cloud_run,
    google_project_iam_member.ui_log_writer,
    google_logging_project_bucket_config.default,
    google_logging_project_bucket_config.required,
  ]
}

# IAP is the only identity allowed to reach either private origin; there is no public invoker.
resource "google_cloud_run_v2_service_iam_member" "iap_invoker" {
  name     = google_cloud_run_v2_service.api.name
  location = google_cloud_run_v2_service.api.location
  project  = var.project_id
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_project_service_identity.iap.email}"

  depends_on = [google_project_iam_member.iap_service_agent]
}

resource "google_cloud_run_v2_service_iam_member" "iap_ui_invoker" {
  name     = google_cloud_run_v2_service.ui.name
  location = google_cloud_run_v2_service.ui.location
  project  = var.project_id
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_project_service_identity.iap.email}"

  depends_on = [google_project_iam_member.iap_service_agent]
}

output "managed_api_url" {
  description = "IAP-protected same-origin UI and governed-RAG API journey URL."
  value       = "https://${var.api_domain}"
}

output "managed_api_ip" {
  description = "Regional HTTPS load-balancer address for the reviewed api_domain DNS record."
  value       = google_compute_address.api.address
}

output "managed_api_iap_audience" {
  description = "Exact IAP JWT audience injected as KB_IAP_AUDIENCE."
  value       = local.iap_audience
}

output "managed_api_backend_service_id" {
  description = "Generated regional backend numeric id used to converge the signed-header audience."
  value       = google_compute_region_backend_service.api.generated_id
}
