"""Governance ports : the A3 Agent Registry concern and the MCP tool catalog.

The managed registry adapter currently refuses advertisement until Agent Runtime has trusted
invocation context. The deployed discovery surface is the governed-RAG HTTP manifest. MCP exposes
only identity-free read schemas.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..domain.models import AgentCard, ToolSpec


@runtime_checkable
class AgentRegistryPort(Protocol):
    def register(self, card: AgentCard) -> None: ...

    def get(self, name: str) -> AgentCard | None: ...

    def list(self) -> list[AgentCard]: ...


@runtime_checkable
class ToolCatalogPort(Protocol):
    def list_tools(self) -> list[ToolSpec]: ...

    def get_tool(self, name: str) -> ToolSpec | None: ...
