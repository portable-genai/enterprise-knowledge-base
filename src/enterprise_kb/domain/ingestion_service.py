"""IngestionService : governed document ingestion (SPEC §5).

Ingests one document into the ACL-aware corpus through the maker-safe pipeline:

    tracer.span("kb.ingest"):
      redaction.redact(content-text)          [PII removed BEFORE indexing, P-04]
      -> guardrail.screen(INPUT)              [blocked -> audit BLOCKED + raise]
      -> ingestion.ingest(document, redacted) [portable parse + governed stores]
      -> citation_store.put(anchored chunks)  [layout anchors for claim resolution]
      -> ledger.upsert(freshness/residency)   [P-03 residency recorded]
      -> audit.record

PII is redacted BEFORE the document is parsed or indexed, so the governed store never
holds raw PII (P-04). A blocked or failed ingest never leaves a half-indexed document:
the guardrail block raises, and an ingestion failure writes a FAILED freshness record so
the next refresh pass retries it.

Pure domain code : no Google Cloud / ADK imports.
"""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from .errors import GuardrailBlockedError
from .freshness_policy import FreshnessPolicy
from .models import (
    AuditEvent,
    Decision,
    Direction,
    Document,
    FreshnessRecord,
    FreshnessStatus,
    GuardrailVerdict,
    IngestResult,
    utcnow,
)


def _as_text(content: bytes) -> str:
    """Best-effort decode of document bytes for the redaction pass.

    DLP de-identification operates on text. Binary documents (PDFs) decode to a lossy
    latin-1 string here purely so the redaction port can scan for embedded PII strings.
    """
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return content.decode("latin-1", errors="replace")


class IngestionService:
    """Ingest a document into the governed store. Signature fixed by SPEC §5."""

    def __init__(
        self,
        ingestion: Any,
        redaction: Any,
        guardrail: Any,
        ledger: Any,
        tracer: Any,
        audit: Any,
        freshness_policy: FreshnessPolicy | None = None,
        citation_store: Any | None = None,
    ) -> None:
        self._ingestion = ingestion
        self._redaction = redaction
        self._guardrail = guardrail
        self._ledger = ledger
        self._tracer = tracer
        self._audit = audit
        self._policy = freshness_policy or FreshnessPolicy()
        # Optional: with no citation store bound, the corpus is still fully ingested and
        # searchable, and citations over it stay at page level.
        self._citation_store = citation_store

    def ingest(
        self,
        document: Document,
        content: bytes,
        mime_type: str,
        actor: str,
        checksum: str = "",
        source_authority: str = "direct",
    ) -> IngestResult:
        """Redact, screen, index and record freshness for ``document`` (SPEC §5)."""
        if source_authority not in {"direct", "registry"}:
            raise ValueError("source_authority must be 'direct' or 'registry'")
        span = self._tracer.span("kb.ingest", action="ingest", actor=actor)
        with span if span is not None else nullcontext():
            return self._ingest_inner(
                document, content, mime_type, actor, checksum, source_authority
            )

    def delete(self, document_id: str, actor: str, tenant: str = "") -> None:
        """Remove a document from the store and the freshness ledger.

        ``tenant`` is the caller's server-verified tenant partition; it scopes the delete so
        a caller cannot remove a document owned by another tenant (multi-tenant isolation).
        """
        span = self._tracer.span("kb.delete", action="delete", actor=actor)
        with span if span is not None else nullcontext():
            self._ingestion.delete(document_id, tenant)
            # Drop the document's anchors too, scoped to the same tenant: a stale anchor
            # would point a reviewer at evidence the corpus no longer holds.
            if self._citation_store is not None:
                self._citation_store.delete(document_id, tenant)
            # Remove the freshness/residency record too, scoped to the caller's tenant, so a
            # deleted document leaves no dangling ledger entry (and no cross-tenant removal).
            self._ledger.delete(document_id, tenant)
            self._write_audit(actor, document_id, "deleted", Decision.ALLOWED, action="delete")

    def _ingest_inner(
        self,
        document: Document,
        content: bytes,
        mime_type: str,
        actor: str,
        checksum: str,
        source_authority: str,
    ) -> IngestResult:
        # 1) Extract source bytes IN MEMORY, then redact before any persistence/index write.
        # This avoids the old PDF corruption path (latin-1 decode -> DLP -> UTF-8 bytes).
        prepared_content, prepared_mime = self._ingestion.prepare_for_redaction(content, mime_type)
        redaction = self._redaction.redact(_as_text(prepared_content))
        redacted_bytes = redaction.text.encode("utf-8")

        # 2) Guardrail screen the document content (INPUT). Blocked -> audit + raise.
        in_verdict: GuardrailVerdict = self._guardrail.screen(redaction.text, Direction.INPUT)
        if not in_verdict.allowed:
            self._write_audit(actor, document.id, "", Decision.BLOCKED)
            raise GuardrailBlockedError(
                in_verdict.reason or f"ingest of {document.id!r} blocked by guardrail"
            )

        # 3) Parse + chunk + index the redacted document into the governed store.
        # Stamp the document's residency to the policy region so the ledger records it.
        document = replace(document, residency_region=self._policy.residency_region)
        result = self._ingestion.ingest(document, redacted_bytes, prepared_mime)

        # Carry the redaction findings onto the result so the caller sees what was masked.
        result = replace(result, redaction_findings=redaction.findings)

        # 4) Persist the parser's anchored chunks so claims can be resolved to a block.
        # A store failure fails the ingest: half-anchored provenance is worse than none,
        # and the FAILED ledger record the caller writes makes the next pass retry it.
        if result.ok and self._citation_store is not None and result.chunk_anchors:
            self._citation_store.put(document, result.chunk_anchors)

        # 5) Record freshness + residency in the ledger.
        if result.ok:
            now = utcnow()
            # Registry-owned material has a refetch authority and obeys the scheduled TTL.
            # Direct local/API material has no registry source to refresh from, so it remains
            # visible until its owner explicitly deletes it rather than expiring into a state the
            # scheduler can neither refresh nor safely tombstone.
            expires_at = (
                self._policy.expires_at(now)
                if source_authority == "registry"
                else datetime.max.replace(tzinfo=UTC)
            )
            record = FreshnessRecord(
                document_id=document.id,
                residency_region=document.residency_region,
                fetched_at=now,
                expires_at=expires_at,
                tenant=document.tenant,
                version=document.version,
                checksum=checksum,
                status=FreshnessStatus.FRESH,
                source_authority=source_authority,
            )
            self._ledger.upsert(record)

        # 6) Audit the ingest.
        decision = Decision.ALLOWED if result.ok else Decision.ESCALATED
        summary = f"chunks={result.chunks} ok={result.ok}"
        self._write_audit(
            actor,
            document.id,
            summary,
            decision,
            metadata={
                "chunks": str(result.chunks),
                "redaction_findings": str(len(result.redaction_findings)),
                "residency_region": document.residency_region,
            },
        )
        return result

    # ------------------------------------------------------------------ #
    # Audit
    # ------------------------------------------------------------------ #
    def _write_audit(
        self,
        actor: str,
        document_id: str,
        redacted_response: str,
        decision: Decision,
        metadata: dict[str, str] | None = None,
        action: str = "ingest",
    ) -> None:
        event = AuditEvent(
            action=action,
            actor=actor,
            decision=decision,
            redacted_prompt=f"document:{document_id}",
            redacted_response=redacted_response,
            metadata=metadata or {},
        )
        # Ingest/delete is not complete until its immutable decision record exists.
        # Propagate sink failures so callers cannot observe a successful governed write
        # that has no audit evidence.
        self._audit.record(event)
