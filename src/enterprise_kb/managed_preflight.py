"""Fail closed on missing named production inputs before managed API or pipeline work.

All primary API/UI/pipeline adapters are implemented. Agent Runtime/A2A is an optional disabled
future surface, guarded independently at its construction and Terraform authorization seams.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

INCOMPLETE_MANAGED_OPERATIONS: tuple[str, ...] = ()


def assert_managed_agent_context_ready(profile: str, context_provider: object | None) -> None:
    """Refuse managed Agent Runtime registration without server-verified request context."""
    if profile in {"gcp", "platform"} and context_provider is None:
        raise RuntimeError(
            "managed Agent Runtime requires a server-injected VerifiedContextProvider; "
            "actor, tenant and ACL principals must never be model-controlled"
        )


def assert_managed_pipeline_ready(settings: Any) -> None:
    """Refuse a managed refresh before any SDK call when required resources are unnamed."""
    if settings.profile not in {"gcp", "platform"}:
        return
    required = {
        "GOOGLE_CLOUD_PROJECT": settings.project_id,
        "KB_CORPUS_BUCKET": settings.storage.corpus_bucket,
        "KB_CONTROL_BUCKET": settings.storage.control_bucket,
        "KB_RAW_SOURCE_BUCKET": settings.storage.raw_source_bucket,
        "KB_ALLOYDB_URI": settings.alloydb.instance_uri,
        "KB_ALLOYDB_USER": settings.alloydb.user,
        "KB_MODEL_ARMOR_TEMPLATE": settings.model_armor.template_id,
        "KB_CORPUS_REGISTRY": settings.corpus.registry_path,
        "KB_ACL_BINDINGS_URI": settings.acl_sync.bindings_uri,
    }
    missing = sorted(
        name
        for name, value in required.items()
        if not str(value).strip() or str(value).strip() == "your-gcp-project"
    )
    if missing:
        raise RuntimeError(
            "managed corpus pipeline configuration is incomplete: " + ", ".join(missing)
        )
    registry = str(settings.corpus.registry_path).strip()
    expected_registry_prefix = f"gs://{settings.storage.control_bucket}/registry/"
    if not registry.startswith(expected_registry_prefix):
        raise RuntimeError(
            "KB_CORPUS_REGISTRY must be a reviewed object under the Terraform-managed "
            f"regional control-input bucket ({expected_registry_prefix}); arbitrary buckets "
            "and the "
            "bundled .test registry are not reachable managed inputs"
        )
    acl_uri = str(settings.acl_sync.bindings_uri).strip()
    expected_acl_prefix = f"gs://{settings.storage.control_bucket}/acl/"
    if not acl_uri.startswith(expected_acl_prefix) or not acl_uri.endswith(".json"):
        raise RuntimeError(
            "KB_ACL_BINDINGS_URI must be a reviewed JSON object under the Terraform-managed "
            f"regional control-input bucket ({expected_acl_prefix})"
        )
    from .adapters.gcp._alloydb import connection_options

    connection_options(settings.alloydb)


def assert_managed_document_sources_ready(settings: Any, documents: list[Any]) -> None:
    """Require raw managed bytes to use the isolated, pipeline-readable source bucket."""
    if settings.profile not in {"gcp", "platform"}:
        return
    expected_prefix = f"gs://{settings.storage.raw_source_bucket}/sources/"
    invalid = sorted(
        str(document.uri)
        for document in documents
        if not str(document.uri).startswith(expected_prefix)
    )
    if invalid:
        raise RuntimeError(
            "managed source document URIs must be private objects under the Terraform-managed "
            f"regional raw-source bucket ({expected_prefix}); invalid: {', '.join(invalid)}"
        )


def _incomplete_operations_for_bindings(
    profile: str, adapters: Mapping[str, Mapping[str, str]] | None
) -> tuple[str, ...]:
    """Return only placeholders that the selected binding map would actually execute."""
    if adapters is None:
        return INCOMPLETE_MANAGED_OPERATIONS
    active_targets = {str(table.get(profile, "")) for table in adapters.values()}
    active: list[str] = []
    for operation in INCOMPLETE_MANAGED_OPERATIONS:
        module_name, class_name, *_ = operation.split(".")
        binding_suffix = f".{module_name}:{class_name}"
        if any(target.endswith(binding_suffix) for target in active_targets):
            active.append(operation)
    return tuple(active)


def assert_managed_profile_ready(
    profile: str,
    adapters: Mapping[str, Mapping[str, str]] | None = None,
    *,
    settings: Any | None = None,
) -> None:
    """Refuse a managed process with active placeholders or unusable DB authentication."""
    incomplete = _incomplete_operations_for_bindings(profile, adapters)
    if profile in {"gcp", "platform"} and incomplete:
        operations = ", ".join(incomplete)
        raise RuntimeError(
            "managed profile is not production ready; implement and integration-test these "
            f"operations before serving {profile}: {operations}"
        )
    if profile in {"gcp", "platform"} and settings is not None:
        from .adapters.gcp._alloydb import connection_options

        connection_options(settings.alloydb)


def main() -> None:
    """Run the same fail-closed preflight used by every production container."""
    from .config import Settings, resolve_profile

    choice = resolve_profile()
    settings = Settings.load()
    assert_managed_profile_ready(choice.profile, settings.adapters, settings=settings)


if __name__ == "__main__":  # pragma: no cover - exercised by the container command
    main()
