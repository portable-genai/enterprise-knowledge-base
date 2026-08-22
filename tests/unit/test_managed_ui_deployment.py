"""The managed browser journey is executable and same-origin behind one IAP edge."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_managed_ui_and_api_share_iap_load_balancer_with_explicit_routes() -> None:
    terraform = (ROOT / "infra/terraform/managed_api.tf").read_text(encoding="utf-8")
    assert 'resource "google_cloud_run_v2_service" "ui"' in terraform
    assert 'resource "google_cloud_run_v2_service" "api"' in terraform
    assert 'resource "google_compute_region_backend_service" "ui"' in terraform
    assert 'resource "google_compute_region_backend_service" "api"' in terraform
    assert terraform.count("network               = google_compute_network.kb.id") >= 2
    assert "default_service = google_compute_region_backend_service.ui.id" in terraform
    for route in ('"/v1/*"', '"/healthz"', '"/.well-known/*"', '"/openapi.json"'):
        assert route in terraform
    assert terraform.count("roles/iap.httpsResourceAccessor") == 2
    assert 'resource "google_project_service_identity" "iap"' in terraform
    assert 'service  = "iap.googleapis.com"' in terraform
    assert 'role    = "roles/iap.serviceAgent"' in terraform
    assert "google_project_service_identity.iap.email" in terraform
    assert 'resource "google_iap_web_region_backend_service_iam_member"' in terraform
    assert 'resource "google_compute_region_url_map" "api"' in terraform
    assert 'resource "google_compute_region_target_https_proxy" "api"' in terraform
    assert 'resource "google_compute_forwarding_rule" "api_https"' in terraform
    assert 'resource "google_compute_address" "api"' in terraform
    assert 'resource "google_compute_global_forwarding_rule"' not in terraform
    assert 'resource "google_compute_global_address" "api"' not in terraform
    assert 'resource "google_compute_backend_service"' not in terraform
    assert '"/projects/%s/global/backendServices/%s"' in terraform
    assert "var.iap_backend_service_id" in terraform
    assert (
        "google_compute_region_backend_service.api.generated_id"
        not in terraform.split('resource "google_compute_region_network_endpoint_group" "api"')[0]
    )
    assert "service = google_cloud_run_v2_service.api.name" in terraform
    assert "depends_on = [google_cloud_run_v2_service.api]" in terraform
    assert "google_compute_region_backend_service.api.generated_id" in terraform
    assert "Do not substitute a /regions/" in terraform
    assert 'resource "google_certificate_manager_certificate" "api"' in terraform
    assert 'resource "google_dns_record_set" "api"' in terraform
    assert "allUsers" not in terraform


def test_regional_edge_has_singapore_proxy_only_subnet() -> None:
    network = (ROOT / "infra/terraform/alloydb.tf").read_text(encoding="utf-8")
    variables = (ROOT / "infra/terraform/variables.tf").read_text(encoding="utf-8")
    assert 'resource "google_compute_subnetwork" "lb_proxy_only"' in network
    assert 'purpose       = "REGIONAL_MANAGED_PROXY"' in network
    assert 'role          = "ACTIVE"' in network
    assert "var.lb_proxy_subnet_cidr" in network
    assert 'variable "lb_proxy_subnet_cidr"' in variables


def test_ui_image_builds_same_origin_and_both_images_are_immutable_regional_inputs() -> None:
    dockerfile = (ROOT / "ui/Dockerfile").read_text(encoding="utf-8")
    variables = (ROOT / "infra/terraform/variables.tf").read_text(encoding="utf-8")
    terraform = (ROOT / "infra/terraform/managed_api.tf").read_text(encoding="utf-8")
    assert 'NEXT_PUBLIC_KB_API_URL=""' in dockerfile
    runtime = dockerfile[dockerfile.index(" AS runtime") :]
    assert 'NEXT_PUBLIC_KB_API_URL=""' in runtime
    assert "npm ci --ignore-scripts" in dockerfile
    assert "mkdir -p public" in dockerfile, "runtime COPY must have a source even with no assets"
    assert 'variable "app_image"' in variables
    assert 'variable "ui_image"' in variables
    assert terraform.count("local.artifact_repo_prefix") >= 2


def test_iap_allows_reviewed_s2s_members_with_exact_tenant_mapping() -> None:
    variables = (ROOT / "infra/terraform/variables.tf").read_text(encoding="utf-8")
    terraform = (ROOT / "infra/terraform/managed_api.tf").read_text(encoding="utf-8")
    example = (ROOT / "infra/terraform/terraform.tfvars.example").read_text(encoding="utf-8")
    assert "user|group|serviceAccount" in variables
    assert 'variable "s2s_service_tenants"' in variables
    assert 'contains(var.iap_accessors, "serviceAccount:${email}")' in variables
    assert "KB_IAP_SERVICE_TENANTS" in terraform
    assert "jsonencode(var.s2s_service_tenants)" in terraform
    assert "serviceAccount:journey-portal@" in example
