"""Ingest + freshness stage of the corpus pipeline.

This is where the corpus concerns meet (SPEC §2, §5). For each registry document the
pipeline routes it through the domain :class:`IngestionService`, which:

1. **Redacts** the content through the :class:`PIIRedactionPort` before it is parsed or
   indexed (P-04), so the governed store never holds raw PII.
2. **Indexes** the redacted document into the store via :class:`IngestionPort`
   (portable in-memory parse + regional Cloud Storage + AlloyDB chunks).
3. **Records freshness + residency** in the ledger via :class:`FreshnessLedgerPort`, with
   an ``expires_at`` computed from ``settings.corpus.ttl_days`` and the pinned region (P-03).

On a read, the search / answer flow can call :func:`ensure_fresh` for any document it is
about to cite: if the ledger record is missing or stale (> TTL) the document is re-fetched
and re-indexed first. The scheduled refresh job (``pipelines.refresh_job``) calls
:func:`refresh_expired` (or :func:`refresh_all`) out of band so reads rarely pay the cost.

The module depends only on the pure domain models, the services, the ports, and
``pipelines.fetch`` : no Google Cloud SDK : so it imports under the on-prem/test profile.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from ..config import Container
from ..domain.errors import GuardrailBlockedError
from ..domain.freshness_policy import FreshnessPolicy
from ..domain.ingestion_service import IngestionService
from ..domain.models import Document, FreshnessRecord, FreshnessStatus, utcnow
from . import fetch as fetch_mod

logger = logging.getLogger(__name__)

_PIPELINE_ACTOR = "corpus-pipeline"


# --------------------------------------------------------------------------- #
# Result types : small, JSON-friendly summaries the refresh job logs and returns.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class SourceOutcome:
    """Outcome of (re-)ingesting a single document."""

    document_id: str
    status: FreshnessStatus
    action: str  # "ingested" | "skipped-fresh" | "failed"
    chunks: int = 0
    detail: str = ""


@dataclass(frozen=True, slots=True)
class RefreshSummary:
    """Aggregate outcome of a refresh pass over a set of documents."""

    outcomes: tuple[SourceOutcome, ...] = field(default_factory=tuple)

    @property
    def ingested(self) -> int:
        return sum(1 for o in self.outcomes if o.action == "ingested")

    @property
    def skipped(self) -> int:
        return sum(1 for o in self.outcomes if o.action == "skipped-fresh")

    @property
    def failed(self) -> int:
        return sum(1 for o in self.outcomes if o.action == "failed")

    @property
    def total(self) -> int:
        return len(self.outcomes)


# --------------------------------------------------------------------------- #
# Registry helpers
# --------------------------------------------------------------------------- #
def load_documents(container: Container) -> list[Document]:
    """Load the document registry referenced by ``settings.corpus.registry_path``."""
    documents = fetch_mod.load_registry(container.settings.corpus.registry_path)
    from ..managed_preflight import assert_managed_document_sources_ready

    assert_managed_document_sources_ready(container.settings, documents)
    return documents


def _index_documents(container: Container) -> dict[str, Document]:
    return {d.id: d for d in load_documents(container)}


def _policy(container: Container) -> FreshnessPolicy:
    return FreshnessPolicy.from_policy(container.settings.policy)


def _service(container: Container) -> IngestionService:
    return IngestionService(
        ingestion=container.ingestion,
        redaction=container.redaction,
        guardrail=container.guardrail,
        ledger=container.ledger,
        tracer=container.tracer,
        audit=container.audit,
        freshness_policy=_policy(container),
        citation_store=container.citation_store,
    )


# --------------------------------------------------------------------------- #
# Core operations
# --------------------------------------------------------------------------- #
def ingest_document(container: Container, document: Document) -> SourceOutcome:
    """Fetch -> redact (P-04) -> index -> upsert freshness, isolating per-document failures.

    The domain :class:`IngestionService` performs the redact/screen/index/ledger steps; on
    any failure (download error, guardrail block, ingestion error) a FAILED record is
    written so the next refresh retries it, and the failure is returned rather than raised.
    """
    try:
        _hide_old_projection_before_refresh(container, document)
        fetched = fetch_mod.fetch_document(document)
        service = _service(container)
        result = service.ingest(
            fetched.document,
            fetched.content,
            fetched.mime_type,
            _PIPELINE_ACTOR,
            checksum=fetched.checksum,
            source_authority="registry",
        )
        if not result.ok:
            return _record_failure(container, document, result.detail)
        logger.info(
            "ingested %s -> chunks=%d redactions=%d",
            document.id,
            result.chunks,
            len(result.redaction_findings),
        )
        return SourceOutcome(
            document_id=document.id,
            status=FreshnessStatus.FRESH,
            action="ingested",
            chunks=result.chunks,
            detail=result.detail,
        )
    except GuardrailBlockedError as exc:
        logger.warning("ingest of %s blocked by guardrail: %s", document.id, exc)
        return _record_failure(container, document, f"blocked: {exc}")
    except Exception as exc:  # noqa: BLE001 - any document must not abort the whole batch
        logger.exception("failed to ingest %s", document.id)
        return _record_failure(container, document, str(exc))


def ensure_fresh(container: Container, document_id: str) -> SourceOutcome:
    """Re-ingest ``document_id`` iff its ledger record is missing or stale; else skip."""
    documents = _index_documents(container)
    document = documents.get(document_id)
    if document is None:
        return SourceOutcome(
            document_id=document_id,
            status=FreshnessStatus.MISSING,
            action="failed",
            detail=f"document '{document_id}' is not in the registry",
        )

    record = container.ledger.get(document_id)
    if record is not None and not _policy(container).is_stale(record):
        return SourceOutcome(
            document_id=document_id,
            status=FreshnessStatus.FRESH,
            action="skipped-fresh",
            detail="ledger record fresh within TTL",
        )
    return ingest_document(container, document)


def refresh_all(container: Container) -> RefreshSummary:
    """Re-ingest **every** document in the registry (the job's ``--full`` mode)."""
    outcomes = [ingest_document(container, d) for d in load_documents(container)]
    return RefreshSummary(outcomes=tuple(outcomes))


def refresh_expired(container: Container, now: datetime | None = None) -> RefreshSummary:
    """Refresh only documents expired in the ledger **or** never ingested."""
    documents = _index_documents(container)
    records = list(container.ledger.all())
    known_ids = {r.document_id for r in records}
    expired_ids = {r.document_id for r in container.ledger.list_expired(now)}
    missing_ids = set(documents) - known_ids
    target_ids = sorted(expired_ids | missing_ids)

    outcomes: list[SourceOutcome] = []
    # Registry removal is a governed tombstone, not a warning: delete indexed chunks and the
    # freshness row so a withdrawn source cannot remain retrievable indefinitely.
    service = _service(container)
    for record in records:
        if record.document_id in documents or record.source_authority != "registry":
            continue
        try:
            service.delete(record.document_id, actor=_PIPELINE_ACTOR, tenant=record.tenant)
            outcomes.append(
                SourceOutcome(
                    document_id=record.document_id,
                    status=FreshnessStatus.MISSING,
                    action="deleted",
                    detail="source removed from reviewed registry; indexed copy tombstoned",
                )
            )
        except Exception as exc:  # noqa: BLE001 - report and keep processing other sources
            logger.exception("failed to tombstone removed registry document %s", record.document_id)
            outcomes.append(
                _record_failure(
                    container,
                    Document(
                        id=record.document_id,
                        title=record.document_id,
                        uri="",
                        tenant=record.tenant,
                    ),
                    str(exc),
                )
            )
    for document_id in target_ids:
        document = documents.get(document_id)
        if document is None:
            logger.warning(
                "expired ledger document %s is no longer in the registry; skipping",
                document_id,
            )
            continue
        outcomes.append(ingest_document(container, document))
    return RefreshSummary(outcomes=tuple(outcomes))


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #
def _hide_old_projection_before_refresh(container: Container, document: Document) -> None:
    """Expire the old projection before any cross-store work can partially fail.

    GCS fetch/write and AlloyDB cannot share one transaction. The retrieval query joins only
    FRESH, unexpired ledger rows, so this first mutation makes a prior version invisible. A
    process crash during fetch/redaction/indexing therefore leaves the document retryable and
    hidden rather than serving stale evidence. Successful ingest is the only path back to FRESH.
    """
    now = utcnow()
    container.ledger.upsert(
        FreshnessRecord(
            document_id=document.id,
            residency_region=container.settings.region,
            fetched_at=now,
            expires_at=now,
            tenant=document.tenant,
            version=document.version,
            status=FreshnessStatus.FAILED,
            source_authority="registry",
        )
    )


def _record_failure(container: Container, document: Document, detail: str) -> SourceOutcome:
    """Write a FAILED freshness record (already-expired) so the next pass retries it."""
    now = utcnow()
    try:
        container.ledger.upsert(
            FreshnessRecord(
                document_id=document.id,
                residency_region=container.settings.region,
                fetched_at=now,
                expires_at=now,  # already expired => eligible for the next refresh
                tenant=document.tenant,
                version=document.version,
                status=FreshnessStatus.FAILED,
                source_authority="registry",
            )
        )
    except Exception:  # noqa: BLE001 - ledger write failure must not mask the original error
        logger.exception("failed to record FAILED ledger entry for %s", document.id)
    return SourceOutcome(
        document_id=document.id,
        status=FreshnessStatus.FAILED,
        action="failed",
        detail=detail,
    )
