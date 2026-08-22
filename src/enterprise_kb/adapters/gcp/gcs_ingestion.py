"""Portable parser + regional GCS persistence for managed corpus ingestion.

The parser is the same portable implementation used by the local profile. Managed GCP adds
ADC-authenticated, CMEK-defaulted regional object persistence and AlloyDB storage through the
separate CitationStorePort. Raw binary input is parsed in memory first; only extracted,
redacted UTF-8 text reaches this adapter's persistence method.
"""

from __future__ import annotations

import hashlib
from typing import Any

from ...config import Settings
from ...domain.layout import blocks_to_chunks
from ...domain.models import Document, IngestResult
from ..local.document import LocalDocumentParser


class GcsPortableIngestionAdapter:
    """Extract portably, then persist only redacted text in the regional corpus bucket."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._bucket = settings.storage.corpus_bucket
        self._parser = LocalDocumentParser(settings)
        self._storage_client: Any | None = None

    @staticmethod
    def _scoped_id(document_id: str, tenant: str) -> str:
        digest = hashlib.sha256(f"{tenant}\0{document_id}".encode()).hexdigest()
        return f"kb-{digest[:40]}"

    def prepare_for_redaction(self, content: bytes, mime_type: str) -> tuple[bytes, str]:
        """Extract binary formats before redaction without ever persisting their raw bytes."""
        extracted = self._parser.parse(content, mime_type)
        text = "\f".join(extracted.pages)
        if not text.strip():
            raise ValueError("document extraction produced no text; refusing an empty corpus row")
        return text.encode("utf-8"), "text/plain"

    def ingest(self, document: Document, content: bytes, mime_type: str) -> IngestResult:
        """Store and chunk already-redacted UTF-8 text; raw source bytes never enter GCS."""
        if mime_type != "text/plain":
            raise ValueError("managed persistence accepts only prepared redacted text/plain")
        parsed = self._parser.parse(content, mime_type).layout
        chunks = blocks_to_chunks(document.id, parsed)
        blob = (
            self._storage()
            .bucket(self._bucket)
            .blob(f"redacted/{self._scoped_id(document.id, document.tenant)}.txt")
        )
        blob.upload_from_string(content, content_type="text/plain; charset=utf-8")
        return IngestResult(
            document_id=document.id,
            chunks=len(chunks),
            ok=True,
            chunk_anchors=tuple(chunks),
            detail="portable parse; redacted text stored in regional GCS for AlloyDB indexing",
        )

    def delete(self, document_id: str, tenant: str = "") -> None:
        blob = (
            self._storage()
            .bucket(self._bucket)
            .blob(f"redacted/{self._scoped_id(document_id, tenant)}.txt")
        )
        if blob.exists():
            blob.delete()

    def _storage(self) -> Any:
        if self._storage_client is None:
            from google.cloud import storage

            self._storage_client = storage.Client(project=self._settings.project_id)
        return self._storage_client
