"""On-prem placeholder for ``CitationStorePort`` : the Google Distributed Cloud target.

The reversibility proof for the anchor work (P-02, P-12), not a gap: the newest port in
the hexagon ships with the same three families as every older one, so switching
``profile`` to ``onprem`` rebinds the citation store here and fails FAST and loudly
rather than silently degrading anchor-level provenance to page level. The adapter
constructs with no external dependencies and structurally satisfies the same Protocol as
the managed AlloyDB adapter, which is what the contract tests assert. Porting anchors to
an on-premise chunk store is only a matter of filling these bodies in: the domain
resolver and every caller are untouched.
"""

from __future__ import annotations

from collections.abc import Sequence

from ...config import Settings
from ...domain.models import Document, DocumentChunk

_MESSAGE = (
    "On-prem CitationStorePort adapter is a migration placeholder; implement against "
    "your on-premise document-chunk store. Core domain logic is unchanged."
)


class OnPremCitationStoreAdapter:
    """Placeholder citation store for the on-prem (Google Distributed Cloud) profile."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def put(self, document: Document, chunks: Sequence[DocumentChunk]) -> int:
        raise NotImplementedError(_MESSAGE)

    def get(self, document_id: str, tenant: str | None = None) -> list[DocumentChunk]:
        raise NotImplementedError(_MESSAGE)

    def delete(self, document_id: str, tenant: str | None = None) -> None:
        raise NotImplementedError(_MESSAGE)
