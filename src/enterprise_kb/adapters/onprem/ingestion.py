"""On-prem placeholder for ``IngestionPort`` : the Google Distributed Cloud target.

One of the reversibility (P-02, P-12) migration placeholders: in the managed profile
this port binds to portable parse + Cloud Storage + AlloyDB ingestion
adapter; switching ``profile`` to ``onprem`` rebinds it here. The adapter constructs
cleanly with **no external dependencies** and structurally satisfies the same Protocol
as the managed adapter, so the contract tests prove interface parity. Porting document
ingestion to an on-premise parse + retrieval store is *only* a matter of filling these
bodies in : the domain ingest pipeline is unchanged.
"""

from __future__ import annotations

from ...config import Settings
from ...domain.models import Document, IngestResult

_MESSAGE = (
    "On-prem IngestionPort adapter is a migration placeholder; implement against your "
    "on-premise platform. Core domain logic is unchanged."
)


class OnPremIngestionAdapter:
    """Placeholder ingestion adapter for the on-prem (Google Distributed Cloud) profile."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def prepare_for_redaction(self, content: bytes, mime_type: str) -> tuple[bytes, str]:
        raise NotImplementedError(_MESSAGE)

    def ingest(self, document: Document, content: bytes, mime_type: str) -> IngestResult:
        raise NotImplementedError(_MESSAGE)

    def delete(self, document_id: str, tenant: str = "") -> None:
        raise NotImplementedError(_MESSAGE)
