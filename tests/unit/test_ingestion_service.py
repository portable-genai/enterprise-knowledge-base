"""Unit tests for IngestionService : the SPEC §5 ingest pipeline.

Pipeline (SPEC §5):
    redact(content) -> guardrail.screen(INPUT)
      -> [if blocked: audit BLOCKED + raise GuardrailBlockedError]
      -> ingestion.ingest(document, redacted_content) -> ledger.upsert(freshness) -> audit

PII is redacted BEFORE the document is parsed or indexed (P-04); residency is recorded
in the ledger (P-03). These tests use only in-memory fakes (no Google Cloud SDK).
"""

from __future__ import annotations

import pytest
from tests.conftest import BlockingGuardrail, load_service
from tests.fixtures import sample_docs

from enterprise_kb.domain.errors import GuardrailBlockedError
from enterprise_kb.domain.models import Decision, FreshnessStatus, IngestResult

ACTOR = "kb-admin@bank.test"
DOC = sample_docs.SAMPLE_DOCUMENTS[0]
CONTENT = sample_docs.SAMPLE_DOCUMENT_CONTENT
MIME = sample_docs.SAMPLE_MIME_TYPE


def test_ingest_indexes_and_records_freshness(ingestion_service, ingestion, ledger):
    result = ingestion_service.ingest(DOC, CONTENT, MIME, actor=ACTOR, checksum="abc123")

    assert isinstance(result, IngestResult)
    assert result.ok is True
    assert result.chunks >= 1
    assert ingestion.ingested, "the document was never indexed"

    # A fresh, in-region freshness record was written.
    record = ledger.get(DOC.id)
    assert record is not None
    assert record.status is FreshnessStatus.FRESH
    assert record.residency_region == "asia-southeast1"  # P-03
    assert record.checksum == "abc123"
    assert record.source_authority == "direct"
    assert record.expires_at.year == 9999, "direct local/API content survives until explicit delete"


def test_registry_ingest_records_refetch_authority_and_bounded_ttl(ingestion_service, ledger):
    ingestion_service.ingest(
        DOC,
        CONTENT,
        MIME,
        actor=ACTOR,
        source_authority="registry",
    )
    record = ledger.get(DOC.id)
    assert record is not None
    assert record.source_authority == "registry"
    assert record.expires_at.year < 9999


def test_unknown_source_authority_refuses_before_mutation(ingestion_service, ingestion):
    with pytest.raises(ValueError, match="source_authority"):
        ingestion_service.ingest(
            DOC, CONTENT, MIME, actor=ACTOR, source_authority="client-asserted"
        )
    assert ingestion.ingested == []


def test_ingest_redacts_before_indexing(ingestion_service, redaction, ingestion):
    # The content carries an NRIC and an email; the indexed bytes must be de-identified.
    ingestion_service.ingest(DOC, CONTENT, MIME, actor=ACTOR)

    assert redaction.calls, "redaction was never called"
    assert "S7654321Z" in redaction.calls[0]

    _doc, indexed_bytes, _mime = ingestion.ingested[0]
    indexed = indexed_bytes.decode("utf-8")
    assert "S7654321Z" not in indexed, "raw NRIC reached the index (P-04 violation)"
    assert "alice@bank.test" not in indexed, "raw email reached the index (P-04 violation)"


def test_ingest_surfaces_redaction_findings(ingestion_service):
    result = ingestion_service.ingest(DOC, CONTENT, MIME, actor=ACTOR)
    info_types = {f.info_type for f in result.redaction_findings}
    assert "SG_NRIC_FIN" in info_types
    assert "EMAIL_ADDRESS" in info_types


def test_ingest_blocked_input_raises_and_does_not_index(
    ingestion, redaction, ledger, tracer, audit
):
    service = load_service("IngestionService")(
        ingestion, redaction, BlockingGuardrail(block_input=True), ledger, tracer, audit
    )
    with pytest.raises(GuardrailBlockedError):
        service.ingest(DOC, CONTENT, MIME, actor=ACTOR)
    assert ingestion.ingested == [], "a blocked document must never be indexed"
    assert any(e.decision is Decision.BLOCKED for e in audit.events)


def test_ingest_audited(ingestion_service, audit):
    ingestion_service.ingest(DOC, CONTENT, MIME, actor=ACTOR)
    assert audit.events
    event = audit.events[-1]
    assert event.action == "ingest"
    assert event.actor == ACTOR


def test_ingest_fails_closed_when_immutable_audit_sink_fails(
    ingestion, redaction, guardrail, ledger, tracer
):
    class FailingAudit:
        def record(self, _event):
            raise RuntimeError("audit unavailable")

    service = load_service("IngestionService")(
        ingestion, redaction, guardrail, ledger, tracer, FailingAudit()
    )

    with pytest.raises(RuntimeError, match="audit unavailable"):
        service.ingest(DOC, CONTENT, MIME, actor=ACTOR)


def test_ingest_wrapped_in_tracer_span(ingestion_service, tracer):
    ingestion_service.ingest(DOC, CONTENT, MIME, actor=ACTOR)
    assert tracer.spans


def test_delete_removes_document_and_audits(ingestion_service, ingestion, audit):
    ingestion_service.delete(DOC.id, actor=ACTOR)
    assert DOC.id in ingestion.deleted
    assert any(e.action == "delete" for e in audit.events)


# --------------------------------------------------------------------------- #
# Multi-tenant write-path isolation (delete / re-ingest cannot cross tenants).
# --------------------------------------------------------------------------- #
def _mem_settings():
    from enterprise_kb.config import LocalSettings, Settings

    return Settings(
        profile="local",
        local=LocalSettings(db_path=":memory:", audit_path=":memory:", ledger_path=":memory:"),
    )


def _internal_passage(tenant: str) -> object:
    from enterprise_kb.domain.models import AclTag, Citation, RetrievedPassage

    return RetrievedPassage(
        text="shared slug body text",
        citation=Citation(document_id="collide", title="C", uri="u", page=1),
        score=0.9,
        acl_tags=(AclTag(label="classification:internal"),),
        tenant=tenant,
    )


def test_delete_document_is_tenant_scoped():
    # Two tenants hold a document under the SAME id. A delete scoped to one tenant must not
    # remove the other tenant's rows : no cross-tenant delete by id.
    from enterprise_kb.adapters.local.retrieval import LocalFtsRetrievalAdapter
    from enterprise_kb.domain.models import KbQuery

    r = LocalFtsRetrievalAdapter(_mem_settings())
    r.seed([_internal_passage("bank-a"), _internal_passage("bank-b")])

    r.delete_document("collide", tenant="bank-a")

    surviving = {(p.citation.document_id, p.tenant) for p in r.retrieve(KbQuery(text="slug body"))}
    assert ("collide", "bank-b") in surviving, "the other tenant's document was wrongly deleted"
    assert ("collide", "bank-a") not in surviving, "the caller's own tenant row should be gone"


def test_reingest_does_not_clobber_another_tenant():
    # Re-ingesting an id that collides with another tenant's document must not overwrite it.
    from enterprise_kb.adapters.local.ingestion import LocalIngestionAdapter
    from enterprise_kb.domain.models import AclTag, Document, KbQuery

    ing = LocalIngestionAdapter(_mem_settings())
    store = ing._retrieval  # the shared in-memory index this adapter writes to
    internal = (AclTag(label="classification:internal"),)
    doc_b = Document(id="collide", title="B", uri="u", acl_tags=internal, tenant="bank-b")
    doc_a = Document(id="collide", title="A", uri="u", acl_tags=internal, tenant="bank-a")

    ing.ingest(doc_b, b"bank b confidential body", "text/plain")
    ing.ingest(doc_a, b"bank a body", "text/plain")  # same id, different tenant

    tenants = {(p.citation.document_id, p.tenant) for p in store.retrieve(KbQuery(text="body"))}
    assert ("collide", "bank-b") in tenants, "re-ingest clobbered the other tenant's document"
    assert ("collide", "bank-a") in tenants, "the re-ingested document should be present"


def test_ledger_is_tenant_partitioned():
    # The freshness/residency ledger keys on (document_id, tenant): two tenants holding the
    # same id coexist (no clobber of residency/freshness), get + delete are tenant-scoped.
    from datetime import timedelta

    from enterprise_kb.adapters.local.ledger import LocalLedgerAdapter
    from enterprise_kb.domain.models import FreshnessRecord, FreshnessStatus, utcnow

    led = LocalLedgerAdapter(_mem_settings())
    now = utcnow()

    def _rec(tenant: str, region: str) -> FreshnessRecord:
        return FreshnessRecord(
            document_id="policy-x",
            residency_region=region,
            fetched_at=now,
            expires_at=now + timedelta(days=7),
            tenant=tenant,
            version="v1",
            status=FreshnessStatus.FRESH,
        )

    led.upsert(_rec("bank-a", "asia-southeast1"))
    led.upsert(_rec("bank-b", "australia-southeast1"))

    assert len(led.all()) == 2, "same id in two tenants must not clobber to one row"
    rec_a = led.get("policy-x", "bank-a")
    rec_b = led.get("policy-x", "bank-b")
    assert rec_a is not None and rec_a.residency_region == "asia-southeast1"
    assert rec_b is not None and rec_b.residency_region == "australia-southeast1"

    led.delete("policy-x", "bank-a")
    assert led.get("policy-x", "bank-a") is None, "tenant-scoped delete should remove A's row"
    assert led.get("policy-x", "bank-b") is not None, "the other tenant's row must survive"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
