"""Managed model tools consume server-verified identity, never model assertions."""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from enterprise_kb.adapters.gcp.a2a_registry import A2ARegistryAdapter
from enterprise_kb.adapters.gcp.mcp_tool_catalog import McpToolCatalogAdapter
from enterprise_kb.agent import tools as agent_tools
from enterprise_kb.config import Settings
from enterprise_kb.domain.identity import Principal
from enterprise_kb.managed_preflight import assert_managed_agent_context_ready


class _Provider:
    def current_principal(self) -> Principal:
        return Principal(
            subject="verified.user@bank.test",
            principals=("group:reader", "group:risk"),
            tenant="bank-a",
            assurance="iap",
            source="test-verified",
        )


class _RecordingService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def search(self, query: str, **kwargs: Any) -> list[Any]:
        self.calls.append((query, kwargs))
        return []

    def answer(self, query: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append((query, kwargs))
        return {"query": query}


def test_tool_signatures_expose_no_identity_or_tenant_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _RecordingService()
    monkeypatch.setattr(agent_tools, "_kb_service", lambda _container: recorder)
    callables = agent_tools.build_tool_callables(_Provider(), Settings(profile="gcp"))

    assert {fn.__name__ for fn in callables} == {"search_kb", "answer_grounded"}
    forbidden = {"actor", "acl_principals", "tenant", "principal", "user_id"}
    for fn in callables:
        assert forbidden.isdisjoint(inspect.signature(fn).parameters)

    search, answer = callables
    search("policy", top_k=500)
    answer("standard")
    for _query, kwargs in recorder.calls:
        assert kwargs["actor"] == "verified.user@bank.test"
        assert kwargs["acl_principals"] == ("group:reader", "group:risk")
        assert kwargs["tenant"] == "bank-a"
    assert recorder.calls[0][1]["top_k"] == 50


def test_managed_registration_refuses_an_absent_verified_context() -> None:
    for profile in ("gcp", "platform"):
        with pytest.raises(RuntimeError, match="server-injected VerifiedContextProvider"):
            assert_managed_agent_context_ready(profile, None)
    assert_managed_agent_context_ready("local", None)


def test_adjacent_managed_advertisements_are_read_only_and_identity_free() -> None:
    settings = Settings(profile="gcp")
    catalog = McpToolCatalogAdapter(settings)
    assert {tool.name for tool in catalog.list_tools()} == {"search_kb", "answer_grounded"}
    for tool in catalog.list_tools():
        properties = set(tool.input_schema["properties"])
        assert not properties.intersection({"actor", "acl_principals", "tenant", "user_id"})
        assert "query" in tool.input_schema["required"]

    registry = A2ARegistryAdapter(settings)
    assert registry.list() == []
    assert registry.get("enterprise-knowledge-base") is None
    with pytest.raises(RuntimeError, match="advertisement is disabled"):
        registry.register(object())  # type: ignore[arg-type]
