"""Runtime ports : hosting, conversation state and long-term memory.

Primary GCP adapters: **Agent Runtime** (formerly Vertex AI Agent Engine) for the
deployed conversational agent, **Agent Platform Sessions** for per-case state, and
**Agent Platform Memory Bank** for durable caller preferences/facts.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..domain.models import GroundedAnswer, LlmMessage, MemoryItem, Session


@runtime_checkable
class AgentRuntimePort(Protocol):
    def query(self, session: Session, message: str) -> GroundedAnswer:
        """Send a user message to the hosted agent within a session and get a reply."""
        ...

    def health(self) -> bool:
        """Liveness/readiness of the hosted agent runtime."""
        ...


@runtime_checkable
class SessionPort(Protocol):
    def create_session(self, user_id: str, case_id: str | None = None) -> Session: ...

    def get(self, session_id: str, user_id: str) -> Session | None: ...

    def append(self, session_id: str, user_id: str, message: LlmMessage) -> None: ...

    def history(self, session_id: str, user_id: str) -> list[LlmMessage]: ...


@runtime_checkable
class MemoryPort(Protocol):
    def store(self, item: MemoryItem) -> None: ...

    def search(self, query: str, scope: str, top_k: int = 5) -> list[MemoryItem]: ...
