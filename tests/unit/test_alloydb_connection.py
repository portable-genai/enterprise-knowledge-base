"""Managed AlloyDB uses per-workload IAM DB auth and an out-of-band privilege migration."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from enterprise_kb.adapters.gcp._alloydb import connection_options
from enterprise_kb.adapters.gcp.alloydb_citation_store import AlloyDBCitationStoreAdapter
from enterprise_kb.adapters.gcp.alloydb_ledger import AlloyDBLedgerAdapter
from enterprise_kb.adapters.gcp.iam_access_control import IamAccessControlAdapter
from enterprise_kb.config import AlloyDBSettings, Settings

ROOT = Path(__file__).resolve().parents[2]


def _configured() -> AlloyDBSettings:
    return AlloyDBSettings(
        instance_uri="projects/fictional/locations/sg/clusters/kb/instances/primary",
        database="enterprise_kb",
        user="enterprise-kb-app@fictional.iam",
        ip_type="PRIVATE",
    )


def test_connection_options_authorize_short_lived_iam_db_auth_only() -> None:
    assert connection_options(_configured()) == {
        "user": "enterprise-kb-app@fictional.iam",
        "db": "enterprise_kb",
        "ip_type": "PRIVATE",
        "enable_iam_auth": True,
    }
    assert "password" not in AlloyDBSettings.__dataclass_fields__


@pytest.mark.parametrize("field", ("instance_uri", "database", "user", "ip_type"))
def test_incomplete_iam_connection_refuses_before_importing_a_cloud_sdk(field: str) -> None:
    with pytest.raises(RuntimeError, match=field):
        connection_options(replace(_configured(), **{field: ""}))


@pytest.mark.parametrize(
    "adapter_type",
    (IamAccessControlAdapter, AlloyDBCitationStoreAdapter, AlloyDBLedgerAdapter),
)
def test_every_alloydb_adapter_refuses_a_missing_workload_iam_user(
    adapter_type: type[object],
) -> None:
    adapter = adapter_type(Settings(alloydb=replace(_configured(), user="")))
    with pytest.raises(RuntimeError, match="KB_ALLOYDB_USER"):
        adapter._get_engine()  # type: ignore[attr-defined]


def test_every_managed_adapter_enables_connector_iam_auth_without_password() -> None:
    for relative in (
        "src/enterprise_kb/adapters/gcp/alloydb_citation_store.py",
        "src/enterprise_kb/adapters/gcp/alloydb_ledger.py",
        "src/enterprise_kb/adapters/gcp/iam_access_control.py",
    ):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert 'enable_iam_auth=options["enable_iam_auth"]' in text
        assert "password=" not in text
        assert "CREATE TABLE" not in text, "serving/pipeline adapters must not own schema DDL"


def test_terraform_has_distinct_iam_db_users_and_no_database_secret_in_state() -> None:
    terraform = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted((ROOT / "infra/terraform").glob("*.tf"))
    )
    assert 'resource "google_alloydb_user" "workload"' in terraform
    assert 'user_type      = "ALLOYDB_IAM_USER"' in terraform
    assert '["alloydbiamuser", "alloydbsuperuser"]' in terraform
    assert ': ["alloydbiamuser"]' in terraform
    assert "app       = trimsuffix(google_service_account.app.email" in terraform
    assert "pipeline  = trimsuffix(google_service_account.pipeline.email" in terraform
    assert "migration = trimsuffix(google_service_account.migration.email" in terraform
    assert "google_service_account.agent_runtime" not in terraform
    assert terraform.count('"roles/alloydb.databaseUser"') == 3
    assert terraform.count('"roles/serviceusage.serviceUsageConsumer"') == 3
    assert terraform.count('"roles/alloydb.client"') == 3
    assert '"alloydb.iam_authentication" = "on"' in terraform
    for forbidden in (
        "alloydb_password",
        "KB_ALLOYDB_PASSWORD",
        "secret_data",
        "initial_user",
        "secret_key_ref",
    ):
        assert forbidden not in terraform
    assert 'name  = "KB_ALLOYDB_USER"' in terraform
    outputs = (ROOT / "infra/terraform/outputs.tf").read_text(encoding="utf-8")
    assert "alloydb_iam_database_users" in outputs
    migration_guide = (ROOT / "infra/sql/README.md").read_text(encoding="utf-8")
    for workload, migration_role in {
        "app": "app_serving_role",
        "pipeline": "pipeline_role",
    }.items():
        assert workload in (ROOT / "infra/terraform/alloydb.tf").read_text(encoding="utf-8")
        assert f"| `{workload}` | `{migration_role}` |" in migration_guide


def test_fresh_cluster_has_passwordless_bootstrap_and_audited_migration_path() -> None:
    bootstrap = (ROOT / "infra/sql/000_bootstrap_database.sql").read_text(encoding="utf-8")
    runner = (ROOT / "scripts/apply_managed_schema.sh").read_text(encoding="utf-8")
    assert "CREATE ROLE %I NOLOGIN" in bootstrap
    assert "CREATE DATABASE %I OWNER %I" in bootstrap
    assert "REVOKE ALL ON DATABASE %I FROM PUBLIC" in bootstrap
    assert "alloydb-auth-proxy" in runner and "--auto-iam-authn" in runner
    assert "000_bootstrap_database.sql" in runner and "001_principal_acl_tags.sql" in runner
    assert "migration-sha256.txt" in runner and "table-grants.txt" in runner


def test_acl_migration_revokes_public_and_grants_only_scoped_privileges() -> None:
    migration = (ROOT / "infra/sql/001_principal_acl_tags.sql").read_text(encoding="utf-8")
    assert "REVOKE ALL ON SCHEMA public FROM PUBLIC" in migration
    assert "REVOKE CREATE ON SCHEMA public FROM PUBLIC" in migration
    tables = "principal_acl_tags, documents, document_chunks, document_freshness"
    assert f"REVOKE ALL ON TABLE {tables} FROM PUBLIC" in migration
    for table in ("principal_acl_tags", "documents", "document_chunks", "document_freshness"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in migration
        assert f'ALTER TABLE {table} OWNER TO :"schema_owner_role"' in migration
    assert "source_authority TEXT NOT NULL DEFAULT 'direct'" in migration
    assert "ADD COLUMN IF NOT EXISTS source_authority" in migration
    role = "app_serving_role"
    assert f'GRANT USAGE ON SCHEMA public TO :"{role}"' in migration
    assert f'GRANT SELECT ON TABLE {tables} TO :"{role}"' in migration
    assert f'INSERT, UPDATE, DELETE ON TABLE {tables} TO :"{role}"' not in migration
    assert "agent_runtime_role" not in migration
    assert (
        f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {tables} TO :"pipeline_role"' in migration
    )
    assert ':"' in migration, "roles must use psql identifier quoting, never string substitution"


def test_corpus_refresh_uses_pipeline_iam_db_user_and_no_password_secret() -> None:
    scheduler = (ROOT / "infra/terraform/scheduler.tf").read_text(encoding="utf-8")
    assert 'google_alloydb_user.workload["pipeline"].user_id' in scheduler
    assert "KB_ALLOYDB_PASSWORD" not in scheduler


def test_scheduler_invokes_only_the_pipeline_job_with_oauth() -> None:
    scheduler = (ROOT / "infra/terraform/scheduler.tf").read_text(encoding="utf-8")
    variables = (ROOT / "infra/terraform/variables.tf").read_text(encoding="utf-8")
    example = (ROOT / "infra/terraform/terraform.tfvars.example").read_text(encoding="utf-8")
    assert "/jobs/${google_cloud_run_v2_job.freshness_refresh.name}:run" in scheduler
    assert "oauth_token" in scheduler
    assert "https://www.googleapis.com/auth/cloud-platform" in scheduler
    assert 'body = base64encode("{}")' in scheduler
    assert "scheduled-ttl-refresh" not in scheduler
    assert "google_cloud_run_v2_job_iam_member" in scheduler
    assert "kb_refresh_url" not in scheduler
    assert 'variable "kb_refresh_url"' not in variables
    assert "kb_refresh_url" not in example
