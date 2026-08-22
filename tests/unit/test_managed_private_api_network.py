"""Regional guardrail/redaction APIs use explicit private/regional network paths."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_model_armor_and_dlp_have_regional_endpoints_and_exact_private_dns() -> None:
    network = (ROOT / "infra/terraform/model_armor_network.tf").read_text(encoding="utf-8")
    apis = (ROOT / "infra/terraform/apis.tf").read_text(encoding="utf-8")
    assert 'resource "google_network_connectivity_regional_endpoint" "model_armor"' in network
    assert 'access_type       = "REGIONAL"' in network
    assert 'target_google_api = "modelarmor.${var.region}.p.rep.googleapis.com"' in network
    assert 'resource "google_dns_managed_zone" "model_armor"' in network
    assert 'dns_name    = "modelarmor.${var.region}.rep.googleapis.com."' in network
    assert 'dns_name    = "${var.region}.rep.googleapis.com."' not in network
    assert 'resource "google_dns_record_set" "model_armor"' in network
    assert "google_network_connectivity_regional_endpoint.model_armor.address" in network
    assert 'resource "google_network_connectivity_regional_endpoint" "dlp"' in network
    assert 'target_google_api = "dlp.${var.region}.p.rep.googleapis.com"' in network
    assert 'dns_name    = "dlp.${var.region}.rep.googleapis.com."' in network
    assert "google_network_connectivity_regional_endpoint.dlp.address" in network
    assert "networkconnectivity.googleapis.com" in apis and "dns.googleapis.com" in apis


def test_dlp_client_is_explicitly_regional_not_global() -> None:
    adapter = (ROOT / "src/enterprise_kb/adapters/gcp/dlp_redaction.py").read_text(encoding="utf-8")
    assert 'api_endpoint=f"dlp.{self._settings.region}.rep.googleapis.com"' in adapter
