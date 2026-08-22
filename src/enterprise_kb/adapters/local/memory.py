"""Local memory adapter (MemoryPort) : in-process durable caller facts.

The ``local`` profile's stand-in for **Agent Platform Memory Bank**: a small in-process
store of memory items with a substring search, seedable and deterministic. When the
Firestore emulator is opted in (``FIRESTORE_EMULATOR_HOST`` set AND the client lib
imports), the adapter mirrors writes to it; the google client is imported lazily, only
on that branch, so the default path imports no google-cloud package.
"""

from __future__ import annotations

from ...config import Settings
from ...domain.models import MemoryItem
from ._emulator import firestore_emulator_active


class LocalMemoryAdapter:
    """In-process memory store (Firestore-emulator-backed when opted in)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._items: list[MemoryItem] = []
        self._fs = None
        if firestore_emulator_active():
            from google.cloud import firestore  # noqa: PLC0415

            self._fs = firestore.Client(project=settings.project_id or "local")

    def store(self, item: MemoryItem) -> None:
        if self._fs is not None:
            self._fs.collection("memory").document(item.id).set(
                {"id": item.id, "content": item.content, "scope": item.scope}
            )
        self._items.append(item)

    def search(self, query: str, scope: str = "user", top_k: int = 5) -> list[MemoryItem]:
        hits = [i for i in self._items if i.scope == scope and query.lower() in i.content.lower()]
        return hits[:top_k]
