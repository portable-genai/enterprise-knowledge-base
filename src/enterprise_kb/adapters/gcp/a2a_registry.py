"""A2A registry adapter : agent discovery and governance for system A2 (A3).

This reserved adapter is deliberately empty and refuses registration. No reliable immutable
Agent Runtime invocation identity is available yet, so advertising an A2A endpoint would publish
a transport that cannot safely serve. The deployed peer contract is the governed-RAG HTTP manifest.
"""

from __future__ import annotations

from ...config import Settings
from ...domain.models import AgentCard


class A2ARegistryAdapter:
    """Fail-closed placeholder until the trusted managed context bridge exists."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._cards: dict[str, AgentCard] = {}

    # ------------------------------------------------------------------ #
    # AgentRegistryPort
    # ------------------------------------------------------------------ #
    def register(self, card: AgentCard) -> None:
        raise RuntimeError(
            "managed A2A advertisement is disabled until a trusted invocation-context bridge exists"
        )

    def get(self, name: str) -> AgentCard | None:
        return self._cards.get(name)

    def list(self) -> list[AgentCard]:
        return list(self._cards.values())
