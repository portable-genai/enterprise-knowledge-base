"""Managed serving is read-only; the pipeline identity alone mutates the corpus."""

from pathlib import Path

import pytest
from fastapi import HTTPException

from enterprise_kb.agent.agent_card import build_agent_card
from enterprise_kb.agent.tools import READ_ONLY_TOOL_NAMES
from enterprise_kb.api.app import _require_local_write_surface
from enterprise_kb.config import Settings

ROOT = Path(__file__).resolve().parents[2]


def test_nonlocal_api_write_surface_refuses_while_local_demo_remains_portable() -> None:
    for profile in ("gcp", "platform", "onprem"):
        with pytest.raises(HTTPException) as exc:
            _require_local_write_surface(Settings(profile=profile))
        assert exc.value.status_code == 403
        assert "pipeline-only" in str(exc.value.detail)

    assert _require_local_write_surface(Settings(profile="local", profile_explicit=True)) is None
    with pytest.raises(HTTPException):
        _require_local_write_surface(Settings(profile="local", profile_explicit=False))
    app_source = (ROOT / "src/enterprise_kb/api/app.py").read_text(encoding="utf-8")
    assert app_source.count("Depends(_require_local_write_surface)") == 2


def test_agent_and_discovery_surface_are_read_only_in_every_profile() -> None:
    assert set(READ_ONLY_TOOL_NAMES) == {"search_kb", "answer_grounded"}
    for profile in ("local", "gcp", "platform", "onprem"):
        card = build_agent_card(Settings(profile=profile))
        assert {skill.id for skill in card.skills} == {"search_kb", "answer_grounded"}


def test_managed_sql_is_schema_owned_and_pipeline_is_the_only_writer() -> None:
    migration = (ROOT / "infra/sql/001_principal_acl_tags.sql").read_text(encoding="utf-8")
    for table in ("principal_acl_tags", "document_chunks", "document_freshness"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in migration
    for adapter in (
        "alloydb_citation_store.py",
        "alloydb_ledger.py",
        "iam_access_control.py",
    ):
        source = (ROOT / "src/enterprise_kb/adapters/gcp" / adapter).read_text(encoding="utf-8")
        assert "CREATE TABLE" not in source
