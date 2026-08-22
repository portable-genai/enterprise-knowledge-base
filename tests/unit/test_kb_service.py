"""Unit tests for KnowledgeBaseService : the SPEC §5 search + answer pipelines.

Pipeline (SPEC §5):
    redact(query) -> guardrail.screen(INPUT) -> access_control.resolve(principals, tenant)
      -> [no tags: access-denied, empty result, audit ESCALATED]
      -> retrieval.retrieve -> filter_by_tenant + subset/all-of tag filter in the DOMAIN (P-09)
      -> guardrail.screen(OUTPUT) -> audit
    answer additionally:
      -> llm.generate(structured) -> map citations -> self-critique
      -> KbReviewPolicy -> guardrail.screen(OUTPUT) -> audit

These tests use only in-memory fakes (no Google Cloud SDK).
"""

from __future__ import annotations

import pytest
from tests.conftest import BlockingGuardrail, FakeAccessControl, load_service
from tests.fixtures import sample_docs

from enterprise_kb.domain.errors import RetrievalEmptyError
from enterprise_kb.domain.kernel import ReviewLevel
from enterprise_kb.domain.models import Decision, Direction, GroundedAnswer, LlmResponse
from enterprise_kb.domain.policy import AnswerPolicy

ACTOR = "analyst@bank.test"
RETAIL = (sample_docs.RETAIL_PRINCIPAL,)
RISK = (sample_docs.RISK_PRINCIPAL,)
NO_ACCESS = (sample_docs.NO_ACCESS_PRINCIPAL,)


# --------------------------------------------------------------------------- #
# Redaction happens BEFORE retrieval (P-04: minimise PII to model & store).
# --------------------------------------------------------------------------- #
def test_redaction_runs_before_retrieval(kb_service, redaction, retrieval):
    kb_service.search(sample_docs.PII_QUERY, actor=ACTOR, acl_principals=RETAIL)

    assert redaction.calls, "redaction.redact was never called"
    assert "S1234567A" in redaction.calls[0]

    assert retrieval.calls, "retrieval.retrieve was never called"
    query_text = retrieval.calls[0].text
    assert "S1234567A" not in query_text
    assert "jane.doe@example.com" not in query_text


def test_blocked_input_never_reaches_acl_retrieval_or_llm(
    retrieval, access_control, redaction, llm, tracer, audit
):
    blocking = BlockingGuardrail(block_input=True, block_output=False)
    service = load_service("KnowledgeBaseService")(
        retrieval, access_control, blocking, redaction, llm, tracer, audit
    )

    answer = service.answer(sample_docs.SAMPLE_QUERY, actor=ACTOR, acl_principals=RETAIL)

    assert answer.citations == ()
    assert answer.requires_human_review is True
    assert retrieval.calls == []
    assert access_control.calls == []
    assert llm.requests == []
    assert blocking.calls == [(sample_docs.SAMPLE_QUERY, Direction.INPUT)]
    assert [event.decision for event in audit.events] == [Decision.BLOCKED, Decision.BLOCKED]


def test_malicious_query_is_blocked_by_real_local_guardrail_before_retrieval(
    kb_service, retrieval, llm, audit
):
    answer = kb_service.answer(sample_docs.MALICIOUS_QUERY, actor=ACTOR, acl_principals=RETAIL)
    assert answer.citations == ()
    assert retrieval.calls == []
    assert llm.requests == []
    assert audit.events[-1].decision is Decision.BLOCKED


# --------------------------------------------------------------------------- #
# ACL filtering happens in the DOMAIN (P-09).
# --------------------------------------------------------------------------- #
def test_search_filters_passages_by_resolved_acl_tags(kb_service):
    # A retail principal may see only dept:retail / classification:internal passages, so
    # the risk-only and restricted passages must be filtered out in the domain.
    passages = kb_service.search(sample_docs.SAMPLE_QUERY, actor=ACTOR, acl_principals=RETAIL)
    doc_ids = {p.citation.document_id for p in passages}
    assert "policy-cloud-onboarding-v3" in doc_ids
    assert "standard-data-residency-v1" not in doc_ids, "restricted doc leaked to retail caller"


def test_risk_principal_sees_restricted_passages(kb_service):
    passages = kb_service.search(sample_docs.SAMPLE_QUERY, actor=ACTOR, acl_principals=RISK)
    doc_ids = {p.citation.document_id for p in passages}
    # Risk resolves to restricted too, so the data-residency standard is admissible.
    assert "standard-data-residency-v1" in doc_ids


def test_no_acl_tags_returns_nothing_and_audits_escalated(
    retrieval, guardrail, redaction, llm, tracer, audit
):
    # A principal that resolves to NO tags can see nothing : fail-closed.
    service = load_service("KnowledgeBaseService")(
        retrieval, FakeAccessControl(), guardrail, redaction, llm, tracer, audit
    )
    passages = service.search(sample_docs.SAMPLE_QUERY, actor=ACTOR, acl_principals=NO_ACCESS)
    assert passages == []
    assert any(
        e.decision is Decision.ESCALATED and e.metadata.get("access_denied") == "true"
        for e in audit.events
    )


def test_search_fails_closed_when_immutable_audit_sink_fails(
    retrieval, guardrail, redaction, llm, tracer
):
    class FailingAudit:
        def record(self, _event):
            raise RuntimeError("audit unavailable")

    service = load_service("KnowledgeBaseService")(
        retrieval,
        FakeAccessControl(),
        guardrail,
        redaction,
        llm,
        tracer,
        FailingAudit(),
    )

    with pytest.raises(RuntimeError, match="audit unavailable"):
        service.search(sample_docs.SAMPLE_QUERY, actor=ACTOR, acl_principals=RETAIL)


def test_partial_tag_overlap_is_not_admitted_subset_matching(
    guardrail, redaction, llm, tracer, audit
):
    # A passage requires BOTH dept:risk AND classification:restricted. A caller holding only
    # dept:risk overlaps one tag but is NOT a superset, so subset (all-of) matching drops it.
    # (Under the old any-of overlap this passage would have leaked.)
    from tests.conftest import FakeAccessControl, FakeRetrieval

    from enterprise_kb.domain.models import AclTag, Citation, RetrievedPassage

    passage = RetrievedPassage(
        text="restricted risk content",
        citation=Citation(document_id="restricted-1", title="R", uri="u", page=1),
        score=0.9,
        acl_tags=(AclTag(label="dept:risk"), AclTag(label="classification:restricted")),
    )
    service = load_service("KnowledgeBaseService")(
        FakeRetrieval(passages=[passage]),
        FakeAccessControl(mapping={"p": {"dept:risk"}}),
        guardrail,
        redaction,
        llm,
        tracer,
        audit,
    )
    passages = service.search(sample_docs.SAMPLE_QUERY, actor=ACTOR, acl_principals=("p",))
    assert passages == [], "one overlapping tag must not admit a multi-tag passage"


def test_full_tag_superset_is_admitted_subset_matching(guardrail, redaction, llm, tracer, audit):
    # The same passage IS admitted when the caller holds every one of its tags.
    from tests.conftest import FakeAccessControl, FakeRetrieval

    from enterprise_kb.domain.models import AclTag, Citation, RetrievedPassage

    passage = RetrievedPassage(
        text="restricted risk content",
        citation=Citation(document_id="restricted-1", title="R", uri="u", page=1),
        score=0.9,
        acl_tags=(AclTag(label="dept:risk"), AclTag(label="classification:restricted")),
    )
    service = load_service("KnowledgeBaseService")(
        FakeRetrieval(passages=[passage]),
        FakeAccessControl(mapping={"p": {"dept:risk", "classification:restricted"}}),
        guardrail,
        redaction,
        llm,
        tracer,
        audit,
    )
    passages = service.search(sample_docs.SAMPLE_QUERY, actor=ACTOR, acl_principals=("p",))
    assert {p.citation.document_id for p in passages} == {"restricted-1"}


def test_tenant_partition_blocks_other_tenant_but_allows_global(
    guardrail, redaction, llm, tracer, audit
):
    # A caller in tenant "bank-a" sees its own tenant's passages and shared/global ("")
    # passages, but never another tenant's : multi-tenant isolation in the domain.
    from tests.conftest import FakeAccessControl, FakeRetrieval

    from enterprise_kb.domain.models import AclTag, Citation, RetrievedPassage

    def _passage(doc: str, tenant: str) -> RetrievedPassage:
        return RetrievedPassage(
            text=f"{doc} body",
            citation=Citation(document_id=doc, title=doc, uri="u", page=1),
            score=0.9,
            acl_tags=(AclTag(label="classification:internal"),),
            tenant=tenant,
        )

    corpus = [_passage("own", "bank-a"), _passage("other", "bank-b"), _passage("global", "")]
    service = load_service("KnowledgeBaseService")(
        FakeRetrieval(passages=corpus),
        FakeAccessControl(mapping={"p": {"classification:internal"}}),
        guardrail,
        redaction,
        llm,
        tracer,
        audit,
    )
    seen = {
        p.citation.document_id
        for p in service.search(
            sample_docs.SAMPLE_QUERY, actor=ACTOR, acl_principals=("p",), tenant="bank-a"
        )
    }
    assert seen == {"own", "global"}


def test_tenant_document_shadows_same_id_global_without_cross_owner_citation(
    guardrail, redaction, llm, tracer, audit
):
    """An id-only model citation can resolve to exactly one verified owner tenant."""
    from tests.conftest import FakeAccessControl, FakeRetrieval

    from enterprise_kb.domain.models import AclTag, Citation, RetrievedPassage

    def _passage(text: str, tenant: str) -> RetrievedPassage:
        return RetrievedPassage(
            text=text,
            citation=Citation(document_id="policy", title=text, uri=f"urn:{tenant or 'global'}"),
            score=0.9,
            acl_tags=(AclTag(label="classification:internal"),),
            tenant=tenant,
        )

    candidates = [_passage("shared policy", ""), _passage("bank-a policy", "bank-a")]
    service = load_service("KnowledgeBaseService")(
        FakeRetrieval(passages=candidates),
        FakeAccessControl(mapping={"p": {"classification:internal"}}),
        guardrail,
        redaction,
        llm,
        tracer,
        audit,
    )

    tenant_passages = service.search("policy", actor=ACTOR, acl_principals=("p",), tenant="bank-a")
    unpartitioned_passages = service.search("policy", actor=ACTOR, acl_principals=("p",))

    assert [(p.text, p.tenant) for p in tenant_passages] == [("bank-a policy", "bank-a")]
    assert unpartitioned_passages == []


def test_untagged_passage_is_dropped_fail_closed(
    access_control, guardrail, redaction, llm, tracer, audit
):
    # A passage carrying no ACL tags must never be returned (fail-closed, P-09).
    from tests.conftest import FakeRetrieval

    from enterprise_kb.domain.models import Citation, RetrievedPassage

    untagged = RetrievedPassage(
        text="unlabelled content",
        citation=Citation(document_id="ghost", title="Ghost", uri="u", page=1),
        score=0.9,
        acl_tags=(),
    )
    service = load_service("KnowledgeBaseService")(
        FakeRetrieval(passages=[untagged]),
        access_control,
        guardrail,
        redaction,
        llm,
        tracer,
        audit,
    )
    passages = service.search(sample_docs.SAMPLE_QUERY, actor=ACTOR, acl_principals=RETAIL)
    assert passages == []


# --------------------------------------------------------------------------- #
# Answer path: citations mapped from used_document_ids, WITH page numbers.
# --------------------------------------------------------------------------- #
def test_answer_maps_citations_with_pages(kb_service, audit):
    answer = kb_service.answer(sample_docs.SAMPLE_QUERY, actor=ACTOR, acl_principals=RETAIL)

    assert isinstance(answer, GroundedAnswer)
    assert answer.answer
    assert answer.citations, "a grounded answer must carry citations"
    assert all(c.page is not None for c in answer.citations), "page-level citation required"

    primary = next(c for c in answer.citations if c.document_id == sample_docs.PRIMARY_DOCUMENT_ID)
    assert primary.page == sample_docs.PRIMARY_PASSAGE.citation.page
    assert any(e.decision is Decision.ALLOWED for e in audit.events)


def test_answer_citations_only_reference_admitted_documents(kb_service):
    # The answer must never cite a document the retail caller could not see.
    answer = kb_service.answer(sample_docs.SAMPLE_QUERY, actor=ACTOR, acl_principals=RETAIL)
    cited = {c.document_id for c in answer.citations}
    assert "standard-data-residency-v1" not in cited


def test_answer_audit_record_is_redacted(kb_service, audit):
    kb_service.answer(sample_docs.PII_QUERY, actor=ACTOR, acl_principals=RETAIL)
    assert audit.events
    event = audit.events[-1]
    assert "S1234567A" not in event.redacted_prompt
    assert "jane.doe@example.com" not in event.redacted_prompt


def test_generated_answer_is_redacted_before_critique_guardrail_audit_and_return(
    retrieval, access_control, guardrail, redaction, tracer, audit
):
    """PII introduced by generation never reaches another model or an output boundary."""

    class PiiThenCritiqueLlm:
        def __init__(self) -> None:
            self.requests = []

        def generate(self, request):  # type: ignore[no-untyped-def]
            self.requests.append(request)
            if len(self.requests) == 1:
                return LlmResponse(
                    text=(
                        '{"answer":"Contact generated.person@example.com for approval",'
                        f'"used_document_ids":["{sample_docs.PRIMARY_DOCUMENT_ID}"],'
                        '"confidence":0.9}'
                    )
                )
            return LlmResponse(
                text=(
                    '{"grounded":true,"confidence":0.9,'
                    '"caveats":["Ask critic.person@example.com to verify"]}'
                )
            )

    llm = PiiThenCritiqueLlm()
    service = load_service("KnowledgeBaseService")(
        retrieval, access_control, guardrail, redaction, llm, tracer, audit
    )

    answer = service.answer(sample_docs.SAMPLE_QUERY, actor=ACTOR, acl_principals=RETAIL)

    assert "generated.person@example.com" not in answer.answer
    assert len(llm.requests) == 2
    assert "generated.person@example.com" not in llm.requests[1].messages[0].content
    assert all(
        "generated.person@example.com" not in screened
        and "critic.person@example.com" not in screened
        for screened, direction in guardrail.calls
        if direction is Direction.OUTPUT
    )
    assert all("critic.person@example.com" not in caveat for caveat in answer.caveats)
    assert "generated.person@example.com" not in audit.events[-1].redacted_response


def test_answer_wrapped_in_tracer_span(kb_service, tracer):
    kb_service.answer(sample_docs.SAMPLE_QUERY, actor=ACTOR, acl_principals=RETAIL)
    assert tracer.spans, "the answer pipeline must open at least one trace span"


# --------------------------------------------------------------------------- #
# Sensitive-ACL grounding forces human review (P-06).
# --------------------------------------------------------------------------- #
def test_restricted_grounding_forces_human_review(kb_service):
    # A risk principal can ground in the restricted data-residency standard, which is a
    # sensitive ACL tag, so the answer must be ESCALATED even at high confidence.
    answer = kb_service.answer(
        "Where must restricted customer records be stored?", actor=ACTOR, acl_principals=RISK
    )
    assert answer.requires_human_review is True
    assert answer.review_level is ReviewLevel.ENHANCED
    assert "sensitive_classification" in answer.review_reasons


# --------------------------------------------------------------------------- #
# B3 : the review flag cannot be produced False by the pipeline.
# --------------------------------------------------------------------------- #
def test_grounded_answer_can_never_be_produced_without_review(kb_service, audit):
    """RED before the B3 closure: a confident, non-sensitive answer was auto-approved.

    Maker-checker (P-06) is a floor for a synthesised answer: the reader cannot see the
    passages the model dropped, so "the model was confident" is not a control. The flag
    must be True here, with the level left at STANDARD (escalation only raises the bar).
    """
    answer = kb_service.answer(sample_docs.SAMPLE_QUERY, actor=ACTOR, acl_principals=RETAIL)
    assert answer.citations, "this case must exercise the grounded (non-degraded) path"
    assert answer.confidence >= 0.6, "this case must be the confident, non-escalated one"
    assert answer.requires_human_review is True
    assert answer.review_level is ReviewLevel.STANDARD
    assert answer.review_reasons == ()
    # ...and the audit record carries the gate, so a reviewer can reconstruct it.
    event = [e for e in audit.events if e.action == "answer"][-1]
    assert event.metadata["requires_human_review"] == "true"
    assert event.metadata["review_level"] == "standard"


# --------------------------------------------------------------------------- #
# B2 : empty retrieval is a HARD ERROR, not a caveated ungrounded answer.
# --------------------------------------------------------------------------- #
def test_empty_retrieval_raises_instead_of_answering_ungrounded(
    empty_retrieval, access_control, guardrail, redaction, llm, tracer, audit
):
    """RED before the B2 closure: the service returned a citation-free GroundedAnswer."""
    service = load_service("KnowledgeBaseService")(
        empty_retrieval, access_control, guardrail, redaction, llm, tracer, audit
    )
    with pytest.raises(RetrievalEmptyError):
        service.answer(sample_docs.SAMPLE_QUERY, actor=ACTOR, acl_principals=RETAIL)
    assert len(access_control.calls) == 1, "one answer must make one managed ACL lookup"
    # The refusal is audited (ESCALATED) BEFORE the error propagates, so a caught
    # exception cannot make an ungrounded request vanish from the WORM trail.
    answer_events = [e for e in audit.events if e.action == "answer"]
    assert answer_events, "the refused answer must still be audited"
    assert answer_events[-1].decision is Decision.ESCALATED
    assert answer_events[-1].citations == ()


def test_empty_retrieval_degrades_when_the_bank_configures_it(
    empty_retrieval, access_control, guardrail, redaction, llm, tracer, audit
):
    """B4: the same input, a different configured number, a different behavior."""
    service = load_service("KnowledgeBaseService")(
        empty_retrieval,
        access_control,
        guardrail,
        redaction,
        llm,
        tracer,
        audit,
        answer_policy=AnswerPolicy(empty_retrieval_raises=False),
    )
    answer = service.answer(sample_docs.SAMPLE_QUERY, actor=ACTOR, acl_principals=RETAIL)
    assert isinstance(answer, GroundedAnswer)
    assert answer.requires_human_review is True
    assert answer.review_level is ReviewLevel.ENHANCED
    assert answer.confidence <= 0.5
    assert answer.citations == ()


# --------------------------------------------------------------------------- #
# Blocked output: blocked answer + audit BLOCKED + requires_human_review.
# --------------------------------------------------------------------------- #
def test_blocked_output_returns_blocked_answer_and_audits(
    retrieval, access_control, redaction, llm, tracer, audit
):
    blocking = BlockingGuardrail(block_input=False, block_output=True)
    service = load_service("KnowledgeBaseService")(
        retrieval, access_control, blocking, redaction, llm, tracer, audit
    )
    answer = service.answer(sample_docs.SAMPLE_QUERY, actor=ACTOR, acl_principals=RETAIL)
    assert answer.requires_human_review is True
    assert answer.review_level is ReviewLevel.ENHANCED
    assert "guardrail_blocked" in answer.review_reasons
    assert answer.citations == ()
    assert any(e.decision is Decision.BLOCKED for e in audit.events)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
