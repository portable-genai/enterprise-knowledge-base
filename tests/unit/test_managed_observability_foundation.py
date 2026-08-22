"""Static deploy contracts for Logging and Cloud Trace residency foundations."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TF = ROOT / "infra" / "terraform"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_system_logging_buckets_are_imported_only_from_singapore_and_cmek_reconciled() -> None:
    logging = _read(TF / "logging_worm.tf")
    assert logging.count('id = "projects/${var.project_id}/locations/${var.region}/buckets/') == 2
    assert 'bucket_id = "_Default"' in logging
    assert 'bucket_id = "_Required"' in logging
    assert logging.count("kms_key_name = google_kms_crypto_key.kb.id") >= 3
    assert 'resource "google_logging_project_exclusion" "governed_audit_from_default"' in logging
    assert (
        'logName=\\"projects/${var.project_id}/logs/enterprise-knowledge-base-audit\\"' in logging
    )
    assert "google_logging_project_sink.audit_to_worm" in logging


def test_trace_defaults_and_effective_bucket_are_plan_apply_blocking() -> None:
    foundation = _read(TF / "observability_foundation.tf")
    script = _read(ROOT / "scripts/check_managed_observability_foundation.sh")
    kms = _read(TF / "kms.tf")
    services = _read(TF / "apis.tf")
    perimeter = _read(TF / "vpc_sc.tf")
    assert 'data "external" "observability_foundation"' in foundation
    assert "check_managed_observability_foundation.sh" in foundation
    assert "default_storage_location == var.region" in foundation
    assert "default_kms_key == google_kms_crypto_key.kb.id" in foundation
    assert 'trace_bucket_count == "1"' in foundation
    assert "!var.production_mode" in foundation
    assert "gcloud beta observability settings describe" in script
    assert "gcloud beta observability buckets list" in script
    assert 'endswith("/buckets/_Trace")' in script
    assert 'resource "google_project_service_identity" "observability"' in kms
    assert 'resource "google_kms_crypto_key_iam_member" "observability"' in kms
    assert "google_project_service_identity.observability.email" in kms
    assert "observability.googleapis.com" in services
    assert "observability.googleapis.com" in perimeter


def test_active_workloads_wait_for_effective_logging_and_trace_foundations() -> None:
    api = _read(TF / "managed_api.tf")
    scheduler = _read(TF / "scheduler.tf")
    for source in (api, scheduler):
        assert "google_logging_project_bucket_config.default" in source
        assert "google_logging_project_bucket_config.required" in source
    assert "terraform_data.observability_foundation" in api
    assert "terraform_data.observability_foundation" in scheduler


def test_cloud_run_network_iam_uses_materialized_service_identity() -> None:
    iam = _read(TF / "iam.tf")
    assert 'member     = "serviceAccount:${google_project_service_identity.cloud_run.email}"' in iam
    assert "serverless-robot-prod.iam.gserviceaccount.com" not in iam
