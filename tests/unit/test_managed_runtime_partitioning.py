"""Managed runtime state never derives identity from process-local or client assertions."""

from __future__ import annotations

import pytest

from enterprise_kb.adapters.gcp.agent_runtime import AgentRuntimeAdapter
from enterprise_kb.adapters.gcp.genai_eval import GenAiEvalAdapter
from enterprise_kb.adapters.gcp.vertex_memory_bank import _managed_partition
from enterprise_kb.adapters.gcp.vertex_sessions import _assert_session_owner, _audit_session_id
from enterprise_kb.config import Settings
from enterprise_kb.domain.models import Session


def test_managed_session_routing_survives_process_restart_without_index() -> None:
    session_id = _audit_session_id("analyst@bank.test", "case-1")
    _assert_session_owner(session_id, "analyst@bank.test")
    with pytest.raises(PermissionError):
        _assert_session_owner(session_id, "other@bank.test")
    with pytest.raises(ValueError):
        _assert_session_owner(session_id, "")


@pytest.mark.parametrize("scope", ["user", "global", "user::subject", "case:tenant:"])
def test_managed_memory_rejects_unpartitioned_scope(scope: str) -> None:
    with pytest.raises(ValueError):
        _managed_partition(scope)


def test_managed_memory_accepts_explicit_tenant_partition() -> None:
    assert _managed_partition("user:bank.test:analyst") == "user:bank.test:analyst"
    assert _managed_partition("case:bank.test:case-1") == "case:bank.test:case-1"


def test_managed_agent_runtime_refuses_client_session_identity() -> None:
    adapter = AgentRuntimeAdapter(Settings(profile="gcp"))
    with pytest.raises(RuntimeError, match="verified tenant/entitlement"):
        adapter.query(Session(id="s", user_id="asserted-by-client"), "question")


def test_partial_managed_eval_cannot_issue_promotion_verdict() -> None:
    adapter = GenAiEvalAdapter(Settings(profile="gcp"))
    with pytest.raises(RuntimeError, match="LocalOfflineEvalAdapter"):
        adapter.evaluate("golden.jsonl")
