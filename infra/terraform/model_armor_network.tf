# Regional Model Armor calls from Direct VPC/VPC-SC require a Private Service Connect regional
# endpoint and private DNS. Cloud Run resolves the public service name to this subnet address.
resource "google_network_connectivity_regional_endpoint" "model_armor" {
  name        = "enterprise-knowledge-base-model-armor"
  project     = var.project_id
  location    = var.region
  access_type = "REGIONAL"
  network     = google_compute_network.kb.id
  subnetwork  = google_compute_subnetwork.serverless.id
  # PSC targets the PRIVATE REP hostname. Private DNS below maps the public REP hostname
  # used by the client onto this endpoint address.
  target_google_api = "modelarmor.${var.region}.p.rep.googleapis.com"

  depends_on = [google_project_service.required]
}

resource "google_dns_managed_zone" "model_armor" {
  name        = "enterprise-knowledge-base-model-armor"
  project     = var.project_id
  dns_name    = "modelarmor.${var.region}.rep.googleapis.com."
  description = "Exact-host private PSC resolution for regional Model Armor."
  visibility  = "private"

  private_visibility_config {
    networks { network_url = google_compute_network.kb.id }
  }
}

resource "google_dns_record_set" "model_armor" {
  project      = var.project_id
  managed_zone = google_dns_managed_zone.model_armor.name
  name         = google_dns_managed_zone.model_armor.dns_name
  type         = "A"
  ttl          = 300
  rrdatas      = [google_network_connectivity_regional_endpoint.model_armor.address]
}

resource "google_network_connectivity_regional_endpoint" "dlp" {
  name              = "enterprise-knowledge-base-dlp"
  project           = var.project_id
  location          = var.region
  access_type       = "REGIONAL"
  network           = google_compute_network.kb.id
  subnetwork        = google_compute_subnetwork.serverless.id
  target_google_api = "dlp.${var.region}.p.rep.googleapis.com"

  depends_on = [google_project_service.required]
}

resource "google_dns_managed_zone" "dlp" {
  name        = "enterprise-knowledge-base-dlp"
  project     = var.project_id
  dns_name    = "dlp.${var.region}.rep.googleapis.com."
  description = "Exact-host private PSC resolution for regional DLP."
  visibility  = "private"

  private_visibility_config {
    networks { network_url = google_compute_network.kb.id }
  }
}

resource "google_dns_record_set" "dlp" {
  project      = var.project_id
  managed_zone = google_dns_managed_zone.dlp.name
  name         = google_dns_managed_zone.dlp.dns_name
  type         = "A"
  ttl          = 300
  rrdatas      = [google_network_connectivity_regional_endpoint.dlp.address]
}
