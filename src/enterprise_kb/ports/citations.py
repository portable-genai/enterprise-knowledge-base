"""CitationStorePort : the anchored document-chunk store behind claim resolution.

Anchor-level citation needs one thing retrieval does not give you: for a document a
claim was grounded in, the FULL ordered set of its layout blocks, so the deterministic
resolver can decide which block a claim actually came from rather than trusting whichever
passage the ranker happened to return. That is this port.

It is backed by the **existing document-chunk storage**, not a new system of record: on
the ``gcp`` profile the chunks live in the same single-region, CMEK-protected AlloyDB
instance the freshness ledger uses (``alloydb.vector_table``); on ``local`` they live in
the same SQLite file the FTS5 retrieval index uses. The ``onprem`` family is the
fail-fast Google Distributed Cloud placeholder that keeps the exit path honest (P-02).

**Tenancy is part of the contract, not a caller convention.** Every method takes the
caller's server-verified tenant and the adapter must scope to it: a caller in tenant B
asking for a document owned by tenant A gets an empty result, never that document's
blocks. ``tenant=None`` is trusted local tooling (the offline CLI or eval gate) and is
unscoped. The empty string is the exact shared/global tenant, so looking up a global
document never widens into every tenant's colliding document id.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from ..domain.models import Document, DocumentChunk


@runtime_checkable
class CitationStorePort(Protocol):
    def put(self, document: Document, chunks: Sequence[DocumentChunk]) -> int:
        """Replace the stored document metadata and anchors with ``chunks``.

        Replace, not append: a re-ingest of a document must not leave the previous
        parse's blocks behind, or a claim could resolve to an anchor that no longer
        exists in the source. Returns the number of chunks stored.
        """
        ...

    def get(self, document_id: str, tenant: str | None = None) -> list[DocumentChunk]:
        """Return the document's anchored chunks in ordinal order, scoped to ``tenant``.

        Fail-closed: a document owned by another tenant resolves to ``[]``, which the
        domain treats as "no anchors available" and degrades to page-level citation.
        """
        ...

    def delete(self, document_id: str, tenant: str | None = None) -> None:
        """Remove the stored anchors for ``(document_id, tenant)``."""
        ...
