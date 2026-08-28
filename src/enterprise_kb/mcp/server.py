"""Serve the governed tool catalog A2 already declares, over MCP 2026-07-28.

The catalog declared two governed tools and served neither: there was no MCP server process
anywhere in the fleet. This supplies the callables that answer the existing catalog and declares
nothing new. `hex_service_kit.mcpserve.bind` refuses a mismatch in either direction at start-up.

**Entitlement is the reason this module is short and deliberate.** A2's whole job is ACL-filtered
retrieval: `search` and `answer` take the caller's entitlement principals and the tenant, and
filtering is fail-closed, so an empty principal sees untagged public data and nothing else. MCP
stdio verifies no end user at all, so no principals are supplied and no tenant is asserted.

That is a real limitation and it is the correct one. An MCP caller reads the public corpus. If
this module ever passed entitlements to widen what a tool returns, it would be manufacturing an
authorization decision the transport cannot support, which is the one change it must never make.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from hex_service_kit import mcpserve

from ..api import deps

#: The tools this module answers, as data, so a test can hold it against the catalog without
#: starting a server or importing the MCP SDK.
HANDLER_NAMES: tuple[str, ...] = ("search_kb", "answer_grounded")


def _filters(arguments: Mapping[str, Any]) -> dict[str, str]:
    raw = arguments.get("filters") or {}
    return {str(k): str(v) for k, v in raw.items()} if isinstance(raw, Mapping) else {}


def build_handlers(actor: str) -> dict[str, mcpserve.Handler]:
    """Bind each declared tool to the service that already performs it.

    No `acl_principals` and no `tenant`: see the module docstring. The domain applies its
    fail-closed filter to an empty principal, which is what an unauthenticated transport should
    get.
    """

    def search_kb(**arguments: Any) -> Any:
        return deps.get_kb_service().search(
            str(arguments.get("query", "")),
            actor=actor,
            acl_principals=(),
            tenant="",
            top_k=int(arguments.get("top_k") or 5),
            filters=_filters(arguments),
        )

    def answer_grounded(**arguments: Any) -> Any:
        return deps.get_kb_service().answer(
            str(arguments.get("query", "")),
            actor=actor,
            acl_principals=(),
            tenant="",
            filters=_filters(arguments),
        )

    return {"search_kb": search_kb, "answer_grounded": answer_grounded}


def build_server(actor: str, *, with_audit_tools: bool = True) -> Any:
    """Build the MCP server for A2's catalog, refusing on any catalog/handler mismatch.

    ``with_audit_tools`` adds the kit's two READ-ONLY evidence tools, so a client that can reach
    this service can also verify and carry out its trail. Read-only is enforced in the kit.
    """
    container = deps.get_container()
    return mcpserve.build_server(
        name="enterprise-knowledge-base",
        version=str(getattr(container.settings, "version", "") or "0.0.1"),
        catalog=container.tool_catalog,
        handlers=build_handlers(actor),
        audit_store=container.audit if with_audit_tools else None,
    )
