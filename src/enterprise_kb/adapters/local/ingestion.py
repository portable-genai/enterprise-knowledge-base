"""Local ingestion adapter (IngestionPort) : parse + index into the SQLite FTS5 store.

The ``local`` profile's stand-in for **regional GCS + AlloyDB indexing**: it parses the
(already-redacted) document content with the shared portable
layout-aware parser, projects the layout blocks into anchored
:class:`~enterprise_kb.domain.models.DocumentChunk` rows, and indexes one passage per
BLOCK (not per page) into the same SQLite FTS5 store the local retrieval adapter reads,
each carrying the document's ACL tags and the block's anchor and bounding box. An ingest
therefore makes a document searchable *and* anchor-citable in the same local run.

The anchored chunks also ride back on the :class:`IngestResult`, which is what the domain
:class:`~enterprise_kb.domain.ingestion_service.IngestionService` persists through the
``CitationStorePort``. SDK-free and deterministic; there is no Google emulator for
managed API, so this path is unconditional.
"""

from __future__ import annotations

from ...config import Settings
from ...domain.layout import blocks_to_chunks
from ...domain.models import (
    Citation,
    Document,
    DocumentChunk,
    IngestResult,
    RetrievedPassage,
)
from .document import LocalDocumentParser
from .retrieval import LocalFtsRetrievalAdapter


class LocalIngestionAdapter:
    """Index a document into the local FTS5 store with anchored, ACL-tagged passages.

    Shares the same on-disk DB as :class:`LocalFtsRetrievalAdapter` so a ``corpus
    refresh`` (or a CLI ``ingest``) makes documents searchable in the same local run.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._retrieval = LocalFtsRetrievalAdapter(settings)
        self._parser = LocalDocumentParser(settings)

    def prepare_for_redaction(self, content: bytes, mime_type: str) -> tuple[bytes, str]:
        """Extract first so binary documents are never corrupted by decode/re-encode."""
        extracted = self._parser.parse(content, mime_type)
        text = "\f".join(extracted.pages)
        if not text.strip():
            raise ValueError("document extraction produced no text")
        return text.encode("utf-8"), "text/plain"

    def ingest(self, document: Document, content: bytes, mime_type: str) -> IngestResult:
        extract = self._parser.parse(content, mime_type)
        chunks = blocks_to_chunks(document.id, extract.layout)
        passages = [self._passage_for(document, chunk) for chunk in chunks]
        # Re-index this document: drop any prior rows for it WITHIN THIS TENANT, then add the
        # new passages. Scoping the delete by the document's tenant means re-ingesting an id
        # that collides with another tenant's document cannot clobber that other tenant's rows.
        self._retrieval.delete_document(document.id, document.tenant)
        n = self._retrieval.add(passages)
        return IngestResult(
            document_id=document.id,
            chunks=n,
            ok=True,
            redaction_findings=(),
            chunk_anchors=tuple(chunks),
            detail=f"indexed {n} anchored block passage(s) into local FTS5",
        )

    def delete(self, document_id: str, tenant: str = "") -> None:
        self._retrieval.delete_document(document_id, tenant)

    @staticmethod
    def _passage_for(document: Document, chunk: DocumentChunk) -> RetrievedPassage:
        return RetrievedPassage(
            text=chunk.text,
            citation=Citation(
                document_id=document.id,
                title=document.title,
                uri=document.uri,
                version=document.version,
                page=chunk.page,
                snippet=chunk.text[:280],
                score=0.5,
                anchor=chunk.anchor,
                bbox=chunk.bbox,
            ),
            score=0.5,
            acl_tags=document.acl_tags,
            tenant=document.tenant,
        )
