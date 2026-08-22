"""Agent Platform Memory Bank adapter — durable analyst memory for system A2.

Backs the domain ``MemoryPort`` with the managed **Agent Platform Memory Bank** (GA),
accessed through ADK's ``VertexAiMemoryBankService``. Memory Bank holds durable,
cross-session facts and preferences for a knowledge-base caller (e.g. "prefer the
retail policy set"), distinct from per-case Sessions state.

ADK's Memory Bank API is ``async``; the synchronous port methods wrap each call in
``asyncio.run``. The Vertex AI / ADK SDK import is lazy so the on-prem and test profiles
import this module without it installed.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ...config import Settings
from ...domain.models import MemoryItem


class VertexMemoryBankAdapter:
    """Map ADK ``VertexAiMemoryBankService`` entries to domain ``MemoryItem`` records."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._app_name = settings.agent_engine.display_name or "enterprise-knowledge-base"
        self._service: Any | None = None

    # ------------------------------------------------------------------ #
    # Lazy SDK plumbing
    # ------------------------------------------------------------------ #
    def _memory_service(self) -> Any:
        """Return (and cache) the ADK ``VertexAiMemoryBankService``.

        This adapter is an optional future integration seam. The managed reference stack
        deploys no Agent Runtime or Memory Bank; a reasoning-engine id is used only when an
        adopter supplies a separately reviewed verified-context integration.
        """
        if self._service is not None:
            return self._service
        from google.adk.memory import VertexAiMemoryBankService  # lazy

        # verify: https://google.github.io/adk-docs/sessions/memory/#vertexaimemorybankservice
        kwargs: dict[str, Any] = {
            "project": self._settings.project_id,
            "location": self._settings.region,
        }
        resource = self._settings.agent_engine.resource_name
        if resource:
            kwargs["agent_engine_id"] = resource.rsplit("/", 1)[-1] if "/" in resource else resource
        self._service = VertexAiMemoryBankService(**kwargs)
        return self._service

    # ------------------------------------------------------------------ #
    # MemoryPort
    # ------------------------------------------------------------------ #
    def store(self, item: MemoryItem) -> None:
        """Persist a durable memory fact, scoped to a user/case/global identity."""
        _managed_partition(item.scope)
        service = self._memory_service()
        # Memory Bank keys facts to a (user_id, app_name); we use the memory scope as
        # the partition (e.g. "user" or a specific case id) so search can filter.
        asyncio.run(self._store_async(service, item))

    async def _store_async(self, service: Any, item: MemoryItem) -> None:
        # Memory Bank ingests *sessions/conversations* and distils facts. When a raw
        # fact is supplied directly we add it as a single-turn memory; ADK exposes this
        # via ``add_memory`` / ``add_session_to_memory`` depending on SDK version.
        from google.genai import types  # lazy

        content = types.Content(role="user", parts=[types.Part(text=item.content)])
        if hasattr(service, "add_memory"):
            # verify: https://google.github.io/adk-docs/sessions/memory/
            await service.add_memory(
                app_name=self._app_name,
                user_id=item.scope,
                content=content,
            )
        else:  # pragma: no cover - exercised only against the live SDK
            await service.add_session_to_memory(  # type: ignore[attr-defined]
                self._fact_session(item, content)
            )

    def search(self, query: str, scope: str, top_k: int = 5) -> list[MemoryItem]:
        """Semantic recall of durable facts for ``scope``, top-``top_k`` by relevance."""
        _managed_partition(scope)
        service = self._memory_service()
        response = asyncio.run(
            service.search_memory(
                app_name=self._app_name,
                user_id=scope,
                query=query,
            )
        )
        return _to_memory_items(response, scope=scope)[:top_k]

    def _fact_session(self, item: MemoryItem, content: Any) -> Any:
        """Wrap a single fact as a minimal ADK ``Session`` for ``add_session_to_memory``."""
        from google.adk.events import Event  # lazy
        from google.adk.sessions import Session as AdkSession  # lazy

        return AdkSession(
            app_name=self._app_name,
            user_id=item.scope,
            id=item.id,
            events=[Event(author="user", content=content)],
        )


# ---------------------------------------------------------------------- #
# Pure mapping helpers (no SDK types in signatures)
# ---------------------------------------------------------------------- #
def _to_memory_items(response: Any, *, scope: str) -> list[MemoryItem]:
    """Map a Memory Bank search response to domain ``MemoryItem`` records.

    The response exposes ``memories`` (newer SDK) each carrying ``content`` parts and an
    id; we tolerate both that shape and a flat list of fact strings.
    """
    entries = getattr(response, "memories", None)
    if entries is None and isinstance(response, list):
        entries = response
    items: list[MemoryItem] = []
    for idx, entry in enumerate(entries or []):
        text = _entry_text(entry)
        if not text:
            continue
        entry_id = str(getattr(entry, "id", "") or getattr(entry, "name", "") or idx)
        items.append(MemoryItem(id=entry_id, content=text, scope=scope))
    return items


def _entry_text(entry: Any) -> str:
    if isinstance(entry, str):
        return entry
    # ADK MemoryEntry / SearchMemoryResponseEntry carry a Content with text parts.
    content = getattr(entry, "content", None) or getattr(entry, "memory", None)
    if content is None:
        fact = getattr(entry, "fact", None)
        return str(fact) if fact else ""
    parts = getattr(content, "parts", None) or []
    return "".join(getattr(p, "text", "") or "" for p in parts)


def _managed_partition(scope: str) -> str:
    """Accept only explicit tenant-bound user/case partitions, never the literal `user`."""
    parts = scope.split(":", 2)
    if len(parts) != 3 or parts[0] not in {"user", "case"} or not all(parts[1:]):
        raise ValueError(
            "managed memory scope must be user:<tenant>:<subject> or case:<tenant>:<case_id>"
        )
    return scope
