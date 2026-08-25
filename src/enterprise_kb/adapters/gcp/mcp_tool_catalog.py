"""MCP tool catalog adapter : the governed tool surface for system A2.

Backs the domain ``ToolCatalogPort`` with the same two read-only capabilities Agent Runtime
registers. Identity fields are intentionally absent: actor, tenant and ACL entitlements are
resolved from a trusted server context beside tool invocation. Corpus mutation belongs only to
the pipeline identity and is not advertised as an MCP/model capability.

Interop: the catalog speaks **MCP 2026-07-28**. In an ADK deployment these specs are
surfaced to the agent through an ``McpToolset`` connected to an MCP server that fronts the
domain services; here the adapter only *declares* the governed catalog. The ``mcp``
package is imported lazily and only when an actual MCP wire object is requested.
"""

from __future__ import annotations

from typing import Any

from ...config import Settings
from ...domain.models import ToolSpec

# MCP protocol revision this catalog conforms to.
MCP_PROTOCOL_VERSION = "2026-07-28"

_FILTERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "Optional structured retrieval filters (e.g. source_system).",
    "additionalProperties": {"type": "string"},
}


def _build_catalog() -> dict[str, ToolSpec]:
    """Declare only verified-context, read-only tools."""
    return {
        "search_kb": ToolSpec(
            name="search_kb",
            description=(
                "Return ACL-filtered, page-cited passages from the enterprise knowledge "
                "base for a query, scoped to the caller's access entitlements."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural-language query."},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                    "filters": _FILTERS_SCHEMA,
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        ),
        "answer_grounded": ToolSpec(
            name="answer_grounded",
            description=(
                "Synthesise a cited answer over the caller's permitted passages, never "
                "beyond the retrieved set, with a maker-checker review flag."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural-language query."},
                    "filters": _FILTERS_SCHEMA,
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        ),
    }


class McpToolCatalogAdapter:
    """Declarative MCP 2026-07-28 catalog of A2's verified-context read tools."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._catalog: dict[str, ToolSpec] = _build_catalog()

    # ------------------------------------------------------------------ #
    # ToolCatalogPort
    # ------------------------------------------------------------------ #
    def list_tools(self) -> list[ToolSpec]:
        return list(self._catalog.values())

    def get_tool(self, name: str) -> ToolSpec | None:
        return self._catalog.get(name)

    # ------------------------------------------------------------------ #
    # MCP wire helpers (lazy ``mcp`` import : only when actually used)
    # ------------------------------------------------------------------ #
    def as_mcp_tools(self) -> list[Any]:
        """Render the catalog as MCP ``Tool`` objects (MCP 2026-07-28 schema)."""
        from mcp import types as mcp_types  # lazy

        # verify: https://modelcontextprotocol.io/specification/2026-07-28
        return [
            mcp_types.Tool(
                name=spec.name,
                description=spec.description,
                inputSchema=spec.input_schema,
            )
            for spec in self._catalog.values()
        ]
