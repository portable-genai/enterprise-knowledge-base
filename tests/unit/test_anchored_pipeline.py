"""End-to-end anchor precision: ingest -> citation store -> anchored answer.

Drives the REAL local adapter family (layout parser, FTS index, SQLite citation store)
through the REAL domain services, so this is the wiring proof for slices 1 to 4 together:
a document ingested offline is parsed into layout blocks, its anchors are persisted
through the port, and an answer over it carries block-level provenance a reviewer can
open. It also pins the two degradations that must NOT become errors: no citation store
bound, and a store that raises.
"""

from __future__ import annotations

import pytest

from enterprise_kb.adapters.local.access_control import LocalAccessControlAdapter
from enterprise_kb.adapters.local.audit import LocalAppendOnlyAuditAdapter
from enterprise_kb.adapters.local.citation_store import LocalSqliteCitationStore
from enterprise_kb.adapters.local.guardrail import LocalHeuristicGuardrailAdapter
from enterprise_kb.adapters.local.ingestion import LocalIngestionAdapter
from enterprise_kb.adapters.local.ledger import LocalLedgerAdapter
from enterprise_kb.adapters.local.llm import LocalDeterministicLLMAdapter
from enterprise_kb.adapters.local.redaction import LocalRegexRedactionAdapter
from enterprise_kb.adapters.local.retrieval import LocalFtsRetrievalAdapter
from enterprise_kb.adapters.local.tracer import LocalNoopTracerAdapter
from enterprise_kb.config import LocalSettings, Settings
from enterprise_kb.domain.ingestion_service import IngestionService
from enterprise_kb.domain.kb_service import KnowledgeBaseService
from enterprise_kb.domain.models import AclTag, Document, SourceSystem

_DOC_TEXT = (
    "Cloud Provider Onboarding Policy\n"
    "\n"
    "This policy applies to every engagement with an external cloud provider.\n"
    "\f"
    "Due diligence before onboarding\n"
    "\n"
    "Before a cloud provider is onboarded the bank completes a security review and a\n"
    "data residency assessment, and records the outcome in the vendor file.\n"
)

_TAGS = (AclTag(label="dept:retail"), AclTag(label="classification:internal"))


def _settings(tmp_path) -> Settings:  # type: ignore[no-untyped-def]
    """A real (temporary) SQLite file, not ``:memory:``.

    The FTS index, the citation store and the ingestion adapter each open their own
    connection to ``local.db_path``; an in-memory database is private to a connection, so
    only a file makes them the one store the profile actually is.
    """
    db = tmp_path / "kb.db"
    return Settings(
        profile="local",
        local=LocalSettings(
            db_path=str(db),
            audit_path=str(tmp_path / "audit.db"),
            ledger_path=str(tmp_path / "ledger.db"),
        ),
    )


class _RaisingStore:
    """A citation store that is down: every read fails."""

    def put(self, document, chunks):  # type: ignore[no-untyped-def]
        raise RuntimeError("store unavailable")

    def get(self, document_id, tenant=""):  # type: ignore[no-untyped-def]
        raise RuntimeError("store unavailable")

    def delete(self, document_id, tenant=""):  # type: ignore[no-untyped-def]
        raise RuntimeError("store unavailable")


@pytest.fixture
def stack(tmp_path):  # type: ignore[no-untyped-def]
    """One shared Settings so the FTS index and the citation store use the same DB."""
    settings = _settings(tmp_path)
    retrieval = LocalFtsRetrievalAdapter(settings)
    retrieval.seed([])  # start from an empty corpus: only the ingested document is present
    store = LocalSqliteCitationStore(settings)
    ingestion_service = IngestionService(
        ingestion=LocalIngestionAdapter(settings),
        redaction=LocalRegexRedactionAdapter(settings),
        guardrail=LocalHeuristicGuardrailAdapter(settings),
        ledger=LocalLedgerAdapter(settings),
        tracer=LocalNoopTracerAdapter(settings),
        audit=LocalAppendOnlyAuditAdapter(settings),
        citation_store=store,
    )
    document = Document(
        id="policy-cloud-onboarding-test-v1",
        title="Cloud Provider Onboarding Policy",
        uri="https://kb.bank.test/policy/cloud-onboarding",
        source_system=SourceSystem.POLICY_PORTAL,
        acl_tags=_TAGS,
        version="v3",
    )
    result = ingestion_service.ingest(document, _DOC_TEXT.encode("utf-8"), "text/plain", "tester")
    return settings, retrieval, store, result


def _kb(settings: Settings, retrieval, store) -> KnowledgeBaseService:  # type: ignore[no-untyped-def]
    return KnowledgeBaseService(
        retrieval=retrieval,
        access_control=LocalAccessControlAdapter(settings),
        guardrail=LocalHeuristicGuardrailAdapter(settings),
        redaction=LocalRegexRedactionAdapter(settings),
        llm=LocalDeterministicLLMAdapter(settings),
        tracer=LocalNoopTracerAdapter(settings),
        audit=LocalAppendOnlyAuditAdapter(settings),
        citation_store=store,
        citation_policy=settings.policy.citation_policy(),
    )


def test_ingest_persists_anchored_chunks_through_the_port(stack) -> None:  # type: ignore[no-untyped-def]
    _settings_obj, _retrieval, store, result = stack
    assert result.ok
    assert result.chunk_anchors, "the parser's anchored chunks ride back on the result"
    stored = store.get("policy-cloud-onboarding-test-v1", "")
    assert [c.anchor for c in stored] == ["p1#b0", "p1#b1", "p2#b0", "p2#b1"]
    assert all(c.bbox is not None for c in stored)
    assert {c.page for c in stored} == {1, 2}


def test_retrieved_passages_carry_their_block_anchor(stack) -> None:  # type: ignore[no-untyped-def]
    settings, retrieval, store, _result = stack
    service = _kb(settings, retrieval, store)
    passages = service.search(
        "security review before onboarding a cloud provider",
        actor="tester",
        acl_principals=("user:jane@bank.test",),
    )
    assert passages, "the ingested document must be retrievable"
    assert any(p.citation.anchor for p in passages), "block anchors survive the index round trip"


def test_answer_citations_are_anchored_to_a_block(stack) -> None:  # type: ignore[no-untyped-def]
    settings, retrieval, store, _result = stack
    service = _kb(settings, retrieval, store)
    answer = service.answer(
        "What due diligence is required before onboarding a cloud provider?",
        actor="tester",
        acl_principals=("user:jane@bank.test",),
    )
    assert answer.citations
    anchored = [c for c in answer.citations if c.anchor]
    assert anchored, "the answer path must resolve at least one claim to a block"
    for citation in anchored:
        assert citation.bbox is not None, "an anchor without a box cannot be highlighted"
        assert citation.page is not None


def test_tenant_user_resolves_anchor_from_admitted_global_document(stack) -> None:  # type: ignore[no-untyped-def]
    settings, retrieval, store, _result = stack
    service = _kb(settings, retrieval, store)

    answer = service.answer(
        "What due diligence is required before onboarding a cloud provider?",
        actor="tester",
        acl_principals=("user:jane@bank.test",),
        tenant="bank.test",
    )

    assert answer.citations
    assert any(citation.anchor for citation in answer.citations)


def test_answer_without_a_citation_store_stays_page_level(stack) -> None:  # type: ignore[no-untyped-def]
    settings, retrieval, _store, _result = stack
    service = _kb(settings, retrieval, None)
    answer = service.answer(
        "What due diligence is required before onboarding a cloud provider?",
        actor="tester",
        acl_principals=("user:jane@bank.test",),
    )
    assert answer.citations, "provenance is still produced with no citation store bound"
    assert all(c.page is not None for c in answer.citations)


def test_a_failing_citation_store_degrades_instead_of_raising(stack) -> None:  # type: ignore[no-untyped-def]
    settings, retrieval, _store, _result = stack
    service = _kb(settings, retrieval, _RaisingStore())
    answer = service.answer(
        "What due diligence is required before onboarding a cloud provider?",
        actor="tester",
        acl_principals=("user:jane@bank.test",),
    )
    assert answer.citations, "a store outage must not cost the caller their answer"


def test_reingest_replaces_the_previous_anchors(stack) -> None:  # type: ignore[no-untyped-def]
    settings, _retrieval, store, _result = stack
    service = IngestionService(
        ingestion=LocalIngestionAdapter(settings),
        redaction=LocalRegexRedactionAdapter(settings),
        guardrail=LocalHeuristicGuardrailAdapter(settings),
        ledger=LocalLedgerAdapter(settings),
        tracer=LocalNoopTracerAdapter(settings),
        audit=LocalAppendOnlyAuditAdapter(settings),
        citation_store=store,
    )
    shorter = Document(
        id="policy-cloud-onboarding-test-v1",
        title="Cloud Provider Onboarding Policy",
        uri="https://kb.bank.test/policy/cloud-onboarding",
        acl_tags=_TAGS,
        version="v4",
    )
    service.ingest(shorter, b"Only one block now.", "text/plain", "tester")
    assert [c.anchor for c in store.get("policy-cloud-onboarding-test-v1", "")] == ["p1#b0"]


def test_delete_removes_the_anchors_too(stack) -> None:  # type: ignore[no-untyped-def]
    settings, _retrieval, store, _result = stack
    service = IngestionService(
        ingestion=LocalIngestionAdapter(settings),
        redaction=LocalRegexRedactionAdapter(settings),
        guardrail=LocalHeuristicGuardrailAdapter(settings),
        ledger=LocalLedgerAdapter(settings),
        tracer=LocalNoopTracerAdapter(settings),
        audit=LocalAppendOnlyAuditAdapter(settings),
        citation_store=store,
    )
    service.delete("policy-cloud-onboarding-test-v1", actor="tester")
    assert store.get("policy-cloud-onboarding-test-v1", "") == []
