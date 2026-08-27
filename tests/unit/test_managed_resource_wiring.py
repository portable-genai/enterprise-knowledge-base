"""Static contracts for fresh managed pipeline reachability and reviewed resources."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_managed_ingest_uses_portable_parser_gcs_and_alloydb_not_unsupported_sg_apis() -> None:
    config = (ROOT / "config/settings.yaml").read_text(encoding="utf-8")
    terraform = "\n".join(
        path.read_text(encoding="utf-8") for path in (ROOT / "infra/terraform").glob("*.tf")
    )
    assert "gcs_ingestion:GcsPortableIngestionAdapter" in config
    assert "alloydb_retrieval:AlloyDBRetrievalAdapter" in config
    assert "discoveryengine" not in terraform
    assert "google_document_ai_processor" not in terraform


def test_refresh_job_has_private_network_path_and_immutable_image_contract() -> None:
    scheduler = (ROOT / "infra/terraform/scheduler.tf").read_text(encoding="utf-8")
    network = (ROOT / "infra/terraform/alloydb.tf").read_text(encoding="utf-8")
    variables = (ROOT / "infra/terraform/variables.tf").read_text(encoding="utf-8")
    iam = (ROOT / "infra/terraform/iam.tf").read_text(encoding="utf-8")
    assert "vpc_access" in scheduler and 'egress = "ALL_TRAFFIC"' in scheduler
    assert "google_compute_subnetwork.serverless.name" in scheduler
    assert "private_ip_google_access = true" in network
    assert "serverless_subnet_cidr" in variables and "<= 26" in variables
    assert "google_compute_subnetwork_iam_member" in iam
    assert "google_project_service_identity.cloud_run.email" in iam
    assert "serverless-robot-prod.iam.gserviceaccount.com" not in iam
    assert "var.refresh_job_image" in scheduler
    assert "@sha256:" in variables
    assert ":latest" not in scheduler
    assert "ignore_changes" not in scheduler
    assert "encryption_key  = google_kms_crypto_key.kb.id" in scheduler
    assert "google_kms_crypto_key_iam_member.cloud_run" in scheduler


def test_scheduler_injects_exact_managed_resources_and_model_armor_exists() -> None:
    scheduler = (ROOT / "infra/terraform/scheduler.tf").read_text(encoding="utf-8")
    armor = (ROOT / "infra/terraform/model_armor.tf").read_text(encoding="utf-8")
    assert 'name  = "KB_MODEL_ARMOR_TEMPLATE"' in scheduler
    assert "google_model_armor_template.kb.template_id" in scheduler
    assert 'resource "google_model_armor_template" "kb"' in armor
    assert 'enforcement_type                   = "INSPECT_AND_BLOCK"' in armor
    assert "ignore_partial_invocation_failures = false" in armor
    assert 'filter_enforcement = "ENABLED"' in armor
    assert 'name  = "KB_CORPUS_REGISTRY"' in scheduler
    assert "var.corpus_registry_uri" in scheduler
    assert 'name  = "KB_CORPUS_BUCKET"' in scheduler
    assert "google_storage_bucket.corpus.name" in scheduler
    assert 'name  = "KB_RAW_SOURCE_BUCKET"' in scheduler
    assert "google_storage_bucket.raw_sources.name" in scheduler
    assert not (ROOT / ".github/workflows/corpus-refresh.yaml").exists(), (
        "Cloud Scheduler -> reviewed Cloud Run Job is the sole managed refresh path"
    )


def test_worm_sink_unique_writer_can_reach_locked_destination() -> None:
    logging = (ROOT / "infra/terraform/logging_worm.tf").read_text(encoding="utf-8")
    variables = (ROOT / "infra/terraform/variables.tf").read_text(encoding="utf-8")
    example = (ROOT / "infra/terraform/terraform.tfvars.example").read_text(encoding="utf-8")
    assert "unique_writer_identity = true" in logging
    assert 'role    = "roles/logging.bucketWriter"' in logging
    assert "google_logging_project_sink.audit_to_worm.writer_identity" in logging
    assert 'variable "production_mode"' in variables
    assert 'variable "lock_worm_bucket"' in variables
    assert "locked = var.lock_worm_bucket" in logging
    assert "!var.production_mode || var.lock_worm_bucket" in logging
    readiness = (ROOT / "infra/terraform/managed_readiness.tf").read_text(encoding="utf-8")
    assert 'check "production_mode_requires_enforced_controls"' in readiness
    assert 'resource "terraform_data" "production_readiness"' in readiness
    assert "precondition" in readiness
    assert "var.enable_vpc_sc" in readiness
    assert "!var.vpc_sc_dry_run" in readiness
    assert "var.gemini_single_zone_pt_confirmed" in readiness
    assert "production_mode  = false" in example
    assert "lock_worm_bucket = false" in example


def test_logging_cmek_uses_settings_api_identity_and_workloads_have_no_raw_key_access() -> None:
    kms = (ROOT / "infra/terraform/kms.tf").read_text(encoding="utf-8")
    iam = (ROOT / "infra/terraform/iam.tf").read_text(encoding="utf-8")
    assert 'data "google_logging_project_cmek_settings" "logging"' in kms
    assert "data.google_logging_project_cmek_settings.logging.service_account_id" in kms
    assert 'service  = "logging.googleapis.com"' not in kms
    assert (
        'startswith(data.google_logging_project_cmek_settings.logging.service_account_id, "cmek-p")'
        in kms
    )
    assert 'google_kms_crypto_key_iam_member" "app"' not in iam
    assert 'google_kms_crypto_key_iam_member" "pipeline"' not in iam
    assert "roles/cloudkms.cryptoKeyEncrypterDecrypter" not in iam


def test_model_armor_callers_can_sanitize_and_read_the_reviewed_template() -> None:
    iam = (ROOT / "infra/terraform/iam.tf").read_text(encoding="utf-8")
    runtime = (ROOT / "infra/terraform/agent_runtime.tf").read_text(encoding="utf-8")
    assert '"roles/modelarmor.user"' in iam
    assert '"roles/modelarmor.viewer"' in iam
    assert "google_service_account" not in runtime
    assert "google_project_iam_member" not in runtime
    assert "google_alloydb_user" not in runtime


def test_managed_gemini_requires_reviewed_singapore_single_zone_pt() -> None:
    variables = (ROOT / "infra/terraform/variables.tf").read_text(encoding="utf-8")
    api = (ROOT / "infra/terraform/managed_api.tf").read_text(encoding="utf-8")
    scheduler = (ROOT / "infra/terraform/scheduler.tf").read_text(encoding="utf-8")
    config = (ROOT / "config/settings.yaml").read_text(encoding="utf-8")
    terraform = "\n".join(
        path.read_text(encoding="utf-8") for path in (ROOT / "infra/terraform").glob("*.tf")
    )
    assert 'variable "gemini_single_zone_pt_confirmed"' in variables
    assert "default     = false" in variables
    assert "var.gemini_single_zone_pt_confirmed" in api
    assert "Standard PayGo is unsupported" in api
    # The pin must match the reviewed PT order managed_api.tf already names: Singapore
    # single-zone Gemini 3.5 Flash. Until 2026-08-27 this asserted gemini-3.7-flash at the
    # `us` multi-region, so the drift guard was holding the config AGAINST the infra.
    assert "reasoning: gemini-3.5-flash" in config
    assert "triage: gemini-3.5-flash" in config
    assert "location: ${KB_MODEL_LOCATION:-asia-southeast1}" in config
    assert "KB_REGION" in api and "= var.region" in api
    assert 'name  = "KB_REGION"' in scheduler
    assert not (ROOT / "infra/terraform/dlp.tf").exists()
    assert "google_data_loss_prevention_inspect_template" not in terraform


def test_raw_inputs_and_governed_redacted_projection_have_distinct_bucket_iam() -> None:
    storage = (ROOT / "infra/terraform/cloud_storage.tf").read_text(encoding="utf-8")
    iam = (ROOT / "infra/terraform/iam.tf").read_text(encoding="utf-8")
    readiness = (ROOT / "src/enterprise_kb/managed_preflight.py").read_text(encoding="utf-8")
    assert 'resource "google_storage_bucket" "raw_sources"' in storage
    assert 'resource "google_storage_bucket" "corpus"' in storage
    assert 'resource "google_storage_bucket" "control_inputs"' in storage
    assert 'resource "google_storage_bucket_iam_member" "source_publisher"' in storage
    assert "var.source_publisher_service_account_email" in storage
    assert 'role   = "roles/storage.objectViewer"' in storage
    assert 'role   = "roles/storage.objectAdmin"' in storage
    assert "google_storage_bucket.raw_sources.name" in storage
    assert "google_storage_bucket.corpus.name" in storage
    assert "google_storage_bucket.control_inputs.name" in storage
    assert '"roles/storage.objectAdmin"' not in iam
    assert "settings.storage.raw_source_bucket" in readiness
    assert "settings.storage.control_bucket" in readiness
    assert (
        'expected_registry_prefix = f"gs://{settings.storage.control_bucket}/registry/"'
        in readiness
    )
    assert 'expected_acl_prefix = f"gs://{settings.storage.control_bucket}/acl/"' in readiness
    assert 'expected_prefix = f"gs://{settings.storage.raw_source_bucket}/sources/"' in readiness
    assert '"roles/run.invoker"' not in iam, "serving identity does not invoke Cloud Run"


def test_regional_artifact_repository_owns_all_immutable_image_inputs() -> None:
    artifact = (ROOT / "infra/terraform/artifact_registry.tf").read_text(encoding="utf-8")
    api = (ROOT / "infra/terraform/managed_api.tf").read_text(encoding="utf-8")
    scheduler = (ROOT / "infra/terraform/scheduler.tf").read_text(encoding="utf-8")
    services = (ROOT / "infra/terraform/apis.tf").read_text(encoding="utf-8")
    workflow = (ROOT / ".github/workflows/build-managed-images.yaml").read_text(encoding="utf-8")
    assert 'resource "google_artifact_registry_repository" "images"' in artifact
    assert "location      = var.region" in artifact
    assert 'format        = "DOCKER"' in artifact
    assert "kms_key_name  = google_kms_crypto_key.kb.id" in artifact
    assert "google_kms_crypto_key_iam_member.artifact_registry" in artifact
    assert 'role       = "roles/artifactregistry.writer"' in artifact
    kms = (ROOT / "infra/terraform/kms.tf").read_text(encoding="utf-8")
    assert 'resource "google_project_service_identity" "artifact_registry"' in kms
    for service in ("alloydb", "cloud_run"):
        assert f'resource "google_project_service_identity" "{service}"' in kms
        assert f"google_project_service_identity.{service}.email" in kms
    assert 'data "google_storage_project_service_account" "storage"' in kms
    assert "data.google_storage_project_service_account.storage.email_address" in kms
    assert 'service  = "artifactregistry.googleapis.com"' in kms
    assert 'resource "google_kms_crypto_key_iam_member" "artifact_registry"' in kms
    assert "artifactregistry.googleapis.com" in services
    assert "local.artifact_repo_prefix" in api
    assert "local.artifact_repo_prefix" in scheduler
    assert "gcloud auth configure-docker" in workflow
    assert "@%s" in workflow and "managed-image-digests.tfvars" in workflow
    assert "terraform apply" not in workflow


def test_managed_demo_release_is_ordered_two_phase_and_evidence_producing() -> None:
    workflow = (ROOT / ".github/workflows/managed-demo-release.yaml").read_text(encoding="utf-8")
    assert "runs-on: ubuntu-latest" not in workflow
    assert workflow.count("runs-on: [self-hosted, linux, hrz2-vpc]") == 5
    image_workflow = (ROOT / ".github/workflows/build-managed-images.yaml").read_text(
        encoding="utf-8"
    )
    assert "runs-on: [self-hosted, linux, hrz2-vpc]" in image_workflow
    providers = (ROOT / "infra/terraform/providers.tf").read_text(encoding="utf-8")
    variables = (ROOT / "infra/terraform/variables.tf").read_text(encoding="utf-8")
    readme = (ROOT / "infra/terraform/README.md").read_text(encoding="utf-8")
    assert 'backend "gcs" {}' in providers
    assert "environment: managed-bootstrap" in workflow
    assert "environment: managed-release" in workflow
    assert "-target=google_artifact_registry_repository.images" in workflow
    assert "-target=google_artifact_registry_repository_iam_member.publisher" in workflow
    assert "Refuse an ungoverned or non-Singapore state backend" in workflow
    assert "iamConfiguration.uniformBucketLevelAccess.enabled" in workflow
    assert "iamConfiguration.publicAccessPrevention" in workflow
    assert "encryption.defaultKmsKeyName" in workflow
    assert "-var=bootstrap_only=true" in workflow
    assert "managed-image-digests.tfvars" in workflow
    assert "-var=bootstrap_only=false" in workflow
    assert workflow.count("iap_backend_service_id=${backend_id}") == 2
    assert "managed_api_backend_service_id" in workflow
    assert "managed_api_iap_audience" in workflow
    assert "needs: build" in workflow and "needs: apply" in workflow
    assert "scripts/apply_managed_schema.sh" in workflow
    assert "scripts/render_managed_demo_artifacts.py" in workflow
    assert "SOURCE_PUBLISHER_SERVICE_ACCOUNT" in workflow
    assert "CONTROL_PUBLISHER_SERVICE_ACCOUNT" in workflow
    assert 'gcloud run jobs execute "$REFRESH_JOB_NAME"' in workflow
    assert '"$REGION" --wait' in workflow
    assert "X-Dev-Persona" not in workflow
    assert 'variable "bootstrap_only"' in variables
    assert "managed_api_url" in workflow
    assert "two protected environment boundaries" in readme
