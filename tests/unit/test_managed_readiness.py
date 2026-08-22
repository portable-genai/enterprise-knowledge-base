"""Managed serving is authorized only while the incomplete-operation ledger is empty."""

from dataclasses import replace

import pytest

from enterprise_kb.config import (
    AclSyncSettings,
    AlloyDBSettings,
    CorpusSettings,
    ModelArmorSettings,
    Settings,
    StorageSettings,
)
from enterprise_kb.managed_preflight import (
    INCOMPLETE_MANAGED_OPERATIONS,
    assert_managed_pipeline_ready,
    assert_managed_profile_ready,
)


def test_local_and_exit_profiles_remain_available() -> None:
    assert_managed_profile_ready("local")
    assert_managed_profile_ready("onprem")


def test_managed_profiles_are_ready_after_the_acl_lookup_is_implemented() -> None:
    assert INCOMPLETE_MANAGED_OPERATIONS == ()
    for profile in ("gcp", "platform"):
        assert_managed_profile_ready(profile)


def test_managed_preflight_refuses_missing_alloydb_iam_identity() -> None:
    settings = Settings(profile="gcp")

    with pytest.raises(RuntimeError, match="KB_ALLOYDB_USER"):
        assert_managed_profile_ready("gcp", settings=settings)


def test_managed_preflight_accepts_complete_alloydb_connection_inputs() -> None:
    settings = Settings(
        profile="gcp",
        alloydb=replace(
            AlloyDBSettings(),
            instance_uri="projects/fictional/locations/sg/clusters/kb/instances/primary",
            user="enterprise-kb-app@fictional.iam",
        ),
    )

    assert_managed_profile_ready("gcp", settings=settings)


def _pipeline_settings() -> Settings:
    project = "fictional-prod"
    return Settings(
        profile="gcp",
        project_id=project,
        model_armor=ModelArmorSettings(template_id="enterprise-knowledge-base-guardrail"),
        storage=StorageSettings(
            corpus_bucket="enterprise-knowledge-base-corpus-fictional-prod",
            control_bucket="enterprise-knowledge-base-control-fictional-prod",
            raw_source_bucket="enterprise-knowledge-base-raw-fictional-prod",
        ),
        corpus=CorpusSettings(
            registry_path="gs://enterprise-knowledge-base-control-fictional-prod/registry/registry.yaml"
        ),
        acl_sync=AclSyncSettings(
            bindings_uri="gs://enterprise-knowledge-base-control-fictional-prod/acl/bindings.json"
        ),
        alloydb=replace(
            AlloyDBSettings(),
            instance_uri=(
                f"projects/{project}/locations/asia-southeast1/clusters/kb/instances/primary"
            ),
            user="enterprise-kb-pipeline@fictional-prod.iam",
        ),
    )


def test_managed_pipeline_preflight_accepts_all_reviewed_resource_inputs() -> None:
    assert_managed_pipeline_ready(_pipeline_settings())


@pytest.mark.parametrize(
    ("setting", "expected"),
    (
        ("project", "GOOGLE_CLOUD_PROJECT"),
        ("raw_bucket", "KB_RAW_SOURCE_BUCKET"),
        ("control_bucket", "KB_CONTROL_BUCKET"),
        ("armor", "KB_MODEL_ARMOR_TEMPLATE"),
        ("alloydb", "KB_ALLOYDB_URI"),
        ("user", "KB_ALLOYDB_USER"),
        ("registry", "KB_CORPUS_REGISTRY"),
        ("acl", "KB_ACL_BINDINGS_URI"),
    ),
)
def test_managed_pipeline_preflight_refuses_missing_resource_inputs(
    setting: str, expected: str
) -> None:
    settings = _pipeline_settings()
    if setting == "project":
        settings = replace(settings, project_id="your-gcp-project")
    elif setting == "raw_bucket":
        settings = replace(
            settings,
            storage=replace(settings.storage, raw_source_bucket=""),
        )
    elif setting == "control_bucket":
        settings = replace(
            settings,
            storage=replace(settings.storage, control_bucket=""),
        )
    elif setting == "armor":
        settings = replace(settings, model_armor=ModelArmorSettings())
    elif setting == "alloydb":
        settings = replace(settings, alloydb=replace(settings.alloydb, instance_uri=""))
    elif setting == "user":
        settings = replace(settings, alloydb=replace(settings.alloydb, user=""))
    elif setting == "registry":
        settings = replace(settings, corpus=CorpusSettings())
    else:
        settings = replace(settings, acl_sync=AclSyncSettings())
    with pytest.raises(RuntimeError, match=expected):
        assert_managed_pipeline_ready(settings)
