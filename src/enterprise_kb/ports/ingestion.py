"""IngestionPort and FreshnessLedgerPort : governed document ingestion + freshness.

Primary GCP adapters: a portable in-memory parser + **Cloud Storage** for redacted text
and an **AlloyDB** chunk/index store, plus an **AlloyDB**-backed
freshness/residency ledger. PII is redacted BEFORE indexing (P-04); the freshness
ledger is the home of residency (P-03) and the TTL freshness window. On-prem migration
swaps both for placeholder adapters with no change to the domain pipeline.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from ..domain.models import Document, FreshnessRecord, IngestResult


@runtime_checkable
class IngestionPort(Protocol):
    def prepare_for_redaction(self, content: bytes, mime_type: str) -> tuple[bytes, str]:
        """Extract source bytes in memory into UTF-8 text before the redaction boundary."""
        ...

    def ingest(self, document: Document, content: bytes, mime_type: str) -> IngestResult:
        """Chunk and persist ``document`` from already-redacted UTF-8 text."""
        ...

    def delete(self, document_id: str, tenant: str = "") -> None:
        """Remove a document and its chunks from the retrieval store.

        ``tenant`` scopes the deletion to the caller's tenant partition: when set, only a
        document owned by that tenant is removed, so a caller can never delete another
        tenant's document by id. An empty ``tenant`` (trusted local tooling) is unscoped.
        """
        ...


@runtime_checkable
class FreshnessLedgerPort(Protocol):
    def get(self, document_id: str, tenant: str = "") -> FreshnessRecord | None:
        """Return the record for ``(document_id, tenant)``; the ledger is tenant-partitioned."""
        ...

    def upsert(self, record: FreshnessRecord) -> None: ...

    def delete(self, document_id: str, tenant: str = "") -> None:
        """Remove the record for ``(document_id, tenant)`` when a document is deleted."""
        ...

    def list_expired(self, now: datetime | None = None) -> list[FreshnessRecord]:
        """Return ledger records whose ``expires_at`` is in the past (all tenants)."""
        ...

    def all(self) -> list[FreshnessRecord]: ...
