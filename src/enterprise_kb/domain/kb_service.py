"""KnowledgeBaseService : ACL-aware search + grounded answer (SPEC §5).

Owns the search and answer pipelines and calls only ports. The pipelines, in order:

    tracer.span("kb.search"):
      access_control.resolve(principals, tenant) [empty -> access-denied, audit ESCALATED]
      -> retrieval.retrieve                   [ACL-tagged passages]
      -> filter by allowed tags in the DOMAIN (P-09)
      -> guardrail.screen(OUTPUT) over rendered passages
      -> audit.record

    tracer.span("kb.answer"):
      search(...) (as above)                  [empty -> low-confidence caveated answer]
      -> llm.generate(system + passages, structured {answer, used_document_ids, confidence})
      -> map used_document_ids back to retrieved Citations (keep page)
      -> resolve each claim to a layout anchor (page + bounding box) in PURE code
      -> self-critique groundedness pass adjusts confidence/caveats
      -> KbReviewPolicy sets requires_human_review (always) + review_level (escalation)
      -> guardrail.screen(OUTPUT)
      -> audit.record(redacted query + answer)

Fail-closed, not fail-quiet. An answer with no ACL-admitted passage would be an
UNGROUNDED claim, so under the reference policy the orchestrator RAISES
:class:`~enterprise_kb.domain.errors.RetrievalEmptyError` instead of returning a caveated
non-answer (B2); the audit ESCALATED record is written first, so the refusal is on the
record. An adopter that needs the degraded envelope sets
``policy.answer.empty_retrieval_raises: false`` and gets the previous behavior, with the
ungrounded case then owned by the caller. Malformed model JSON and guardrail blocks still
degrade to a safe, review-flagged result.

Pure domain code : no Google Cloud / ADK / FastAPI imports.
"""

from __future__ import annotations

from contextlib import nullcontext
from typing import Any

from . import _grounded as g
from .anchors import anchor_citations
from .errors import RetrievalEmptyError
from .hitl import REASON_UNGROUNDED, KbReviewPolicy
from .kernel import ReviewLevel
from .models import (
    AuditEvent,
    Citation,
    Decision,
    Direction,
    GroundedAnswer,
    GuardrailVerdict,
    RetrievedPassage,
)
from .policy import AnswerPolicy, CitationPolicy
from .prompts import (
    _CITATION_RULES,
    GROUNDED_ANSWER_SYSTEM,
    GROUNDED_ANSWER_USER,
    SELF_CRITIQUE_SYSTEM,
    SELF_CRITIQUE_USER,
)

# JSON schema for the structured grounded-answer response.
_ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "used_document_ids": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number"},
    },
    "required": ["answer", "used_document_ids", "confidence"],
}

# JSON schema for the self-critique groundedness pass.
_CRITIQUE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "grounded": {"type": "boolean"},
        "confidence": {"type": "number"},
        "caveats": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["grounded", "confidence", "caveats"],
}

_EMPTY_RETRIEVAL_CAVEAT = (
    "No permitted passages were retrieved for this query; the answer is not grounded "
    "and must be reviewed by a human before use."
)
_ACCESS_DENIED_CAVEAT = (
    "The caller's principals resolve to no access-control tags, so no corpus content "
    "is visible to them."
)
_BLOCKED_CAVEAT = "The request was blocked by the safety guardrail and was not answered."


def _clamp(value: float) -> float:
    """Clamp a confidence into [0.0, 1.0], defaulting non-numerics to 0.0."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, f))


class KnowledgeBaseService:
    """ACL-aware governed RAG service. Constructor takes explicit ports (SPEC §5)."""

    def __init__(
        self,
        retrieval: Any,
        access_control: Any,
        guardrail: Any,
        redaction: Any,
        llm: Any,
        tracer: Any,
        audit: Any,
        review_policy: KbReviewPolicy | None = None,
        answer_policy: AnswerPolicy | None = None,
        citation_store: Any | None = None,
        citation_policy: CitationPolicy | None = None,
    ) -> None:
        self._retrieval = retrieval
        self._access_control = access_control
        self._guardrail = guardrail
        self._redaction = redaction
        self._llm = llm
        self._tracer = tracer
        self._audit = audit
        self._review = review_policy or KbReviewPolicy()
        self._answer_policy = answer_policy or AnswerPolicy()
        # Optional: with no citation store bound, citations keep page-level provenance.
        self._citation_store = citation_store
        self._citation_policy = citation_policy or CitationPolicy()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def search(
        self,
        query: str,
        actor: str,
        acl_principals: tuple[str, ...] = (),
        tenant: str = "",
        top_k: int = 10,
        filters: dict[str, str] | None = None,
    ) -> list[RetrievedPassage]:
        """Return ACL-filtered passages for ``query`` (no LLM synthesis).

        ``acl_principals`` are the caller's already-entitlement-checked principal ids and
        ``tenant`` the caller's tenant partition; both flow from the server-verified
        :class:`~enterprise_kb.domain.identity.Principal`, never a client assertion.
        """
        span = self._tracer.span("kb.search", action="search", actor=actor)
        with span if span is not None else nullcontext():
            passages, _redacted, _blocked, _denied = self._search_inner(
                query, actor, acl_principals, tenant, top_k, filters
            )
            return passages

    def answer(
        self,
        query: str,
        actor: str,
        acl_principals: tuple[str, ...] = (),
        tenant: str = "",
        top_k: int = 10,
        filters: dict[str, str] | None = None,
    ) -> GroundedAnswer:
        """Synthesise a cited, ACL-grounded answer for ``query`` (SPEC §5).

        Raises:
            RetrievalEmptyError: when no ACL-admitted passage grounds the query and the
                configured answer policy keeps the reference ``empty_retrieval_raises``.
        """
        span = self._tracer.span("kb.answer", action="answer", actor=actor)
        with span if span is not None else nullcontext():
            return self._answer_inner(query, actor, acl_principals, tenant, top_k, filters)

    # ------------------------------------------------------------------ #
    # Search pipeline (shared by answer)
    # ------------------------------------------------------------------ #
    def _search_inner(
        self,
        query: str,
        actor: str,
        acl_principals: tuple[str, ...],
        tenant: str,
        top_k: int,
        filters: dict[str, str] | None,
    ) -> tuple[list[RetrievedPassage], str, GuardrailVerdict | None, bool]:
        """Resolve ACLs, retrieve, filter by tenant + allowed tags, screen, audit.

        Returns ``(admitted_passages, redacted_query, blocking_verdict, access_denied)``. The third
        element is the guardrail verdict when the INPUT query or OUTPUT passages were denied, and
        ``None`` otherwise. The fourth distinguishes a principal with no resolved tags from
        a permitted search that returned no evidence, without querying the managed ACL store
        twice. "Blocked", "access denied" and "nothing found" all yield zero passages but
        are different outcomes. PII is redacted before retrieval so the raw query never
        reaches the retrieval backend (P-04).
        """
        # 1) Redact the query before it touches retrieval or the audit log.
        redacted_q = self._redaction.redact(query).text

        # 2) Screen the inbound query before ACL lookup, retrieval or any model call. Browser/API
        # requests do not traverse the optional ADK callbacks, so the domain owns this invariant.
        # A managed guardrail failure is fail-closed and audited as a blocked search.
        in_verdict: GuardrailVerdict = self._guardrail.screen(redacted_q, Direction.INPUT)
        if not in_verdict.allowed:
            self._audit_search(actor, redacted_q, (), Decision.BLOCKED)
            return [], redacted_q, in_verdict, False
        redacted_q = in_verdict.sanitized_text or redacted_q

        # 3) Resolve principals to the ACL tags they may see. No tags -> access denied.
        allowed_tags = set(self._access_control.resolve(list(acl_principals), tenant))
        if not allowed_tags:
            self._audit_search(actor, redacted_q, (), Decision.ESCALATED, denied=True)
            return [], redacted_q, None, True

        # 4) Retrieve ACL-tagged passages from the store.
        passages = g.retrieve_passages(
            self._retrieval,
            redacted_q,
            allowed_tags=allowed_tags,
            tenant=tenant,
            filters=filters,
            top_k=top_k,
        )

        # 5) Enforce the tenant partition, then filter to the caller's allowed tags :
        # both ACL decisions live here in the domain, never in a retrieval adapter (P-09).
        passages = g.filter_by_tenant(passages, tenant)
        passages = g.prefer_unambiguous_document_owners(passages, tenant)
        admitted = g.filter_by_allowed_tags(passages, allowed_tags)

        # 6) Guardrail screen the rendered passage text on the way out.
        rendered = g.render_passages(admitted)
        out_verdict: GuardrailVerdict = self._guardrail.screen(rendered, Direction.OUTPUT)
        if not out_verdict.allowed:
            self._audit_search(actor, redacted_q, (), Decision.BLOCKED)
            return [], redacted_q, out_verdict, False

        # 7) Audit the search.
        citations = tuple(p.citation for p in admitted)
        self._audit_search(actor, redacted_q, citations, Decision.ALLOWED)
        return admitted, redacted_q, None, False

    # ------------------------------------------------------------------ #
    # Answer pipeline
    # ------------------------------------------------------------------ #
    def _answer_inner(
        self,
        query: str,
        actor: str,
        acl_principals: tuple[str, ...],
        tenant: str,
        top_k: int,
        filters: dict[str, str] | None,
    ) -> GroundedAnswer:
        passages, redacted_q, blocked, access_denied = self._search_inner(
            query, actor, acl_principals, tenant, top_k, filters
        )

        if blocked is not None:
            # A guardrail denial is a block, not an ungrounded query: it returns the
            # blocked envelope (flagged for enhanced review), never the B2 refusal.
            return self._blocked_answer(query, redacted_q, actor, blocked)
        if not passages:
            return self._no_grounding(query, redacted_q, actor, access_denied)

        # Synthesise a grounded answer from the admitted passages.
        passage_block = g.render_passages(passages)
        system = GROUNDED_ANSWER_SYSTEM.format(citation_rules=_CITATION_RULES)
        user = GROUNDED_ANSWER_USER.format(query=redacted_q, passages=passage_block)
        request = g.build_llm_request(
            system_instruction=system,
            user_content=user,
            model=None,  # adapter default => reasoning model gemini-3.5-flash
            response_schema=_ANSWER_SCHEMA,
        )
        response = self._llm.generate(request)
        g.maybe_record_usage(self._tracer, response)

        parsed = g.parse_structured(response)
        answer_text = str(parsed.get("answer") or "").strip()
        used_ids = g.as_str_list(parsed.get("used_document_ids"))
        confidence = _clamp(parsed.get("confidence", 0.0))

        # Treat generated prose as untrusted data.  Redact it before it reaches the
        # deterministic anchor resolver, the second model (self-critique), the output
        # guardrail, the immutable audit sink, or the caller.  Query redaction alone is
        # insufficient: a model can reproduce PII present in retrieved evidence or
        # introduce it independently.
        answer_text = self._redaction.redact(answer_text).text

        # Map used_document_ids back to retrieved Citations (preserve page).
        citations: tuple[Citation, ...] = g.citations_for_document_ids(used_ids, passages)

        # Refine each citation from page level to ANCHOR level. The model returned prose
        # and document ids only; which block grounds which claim is decided here, by pure
        # deterministic code, against the stored layout chunks of that same document.
        citations = self._anchor(answer_text, citations, passages)

        caveats: list[str] = []
        if not answer_text:
            answer_text = (
                "The available passages do not support a confident answer to this "
                "query. Human review is recommended."
            )
            confidence = min(confidence, 0.2)
            caveats.append(_EMPTY_RETRIEVAL_CAVEAT)

        # Self-critique groundedness pass adjusts confidence.
        confidence, critique_caveats = self._self_critique(
            redacted_q, answer_text, passage_block, confidence
        )
        for critique_caveat in critique_caveats:
            governed_caveat = self._redaction.redact(critique_caveat).text
            caveat_verdict: GuardrailVerdict = self._guardrail.screen(
                governed_caveat, Direction.OUTPUT
            )
            if not caveat_verdict.allowed:
                return self._blocked_answer(query, redacted_q, actor, caveat_verdict)
            caveats.append(caveat_verdict.sanitized_text or governed_caveat)

        # HITL policy (P-06): review is the floor for every synthesised answer; low
        # confidence or a sensitive ACL grounding tag only raises the level to ENHANCED.
        grounding_tags = {t.label for p in passages for t in p.acl_tags}
        review = self._review.evaluate(confidence=confidence, grounding_tags=grounding_tags)

        # Guardrail screen (OUTPUT) on the assembled answer text.
        out_verdict: GuardrailVerdict = self._guardrail.screen(answer_text, Direction.OUTPUT)
        if not out_verdict.allowed:
            return self._blocked_answer(query, redacted_q, actor, out_verdict)
        final_answer_text = out_verdict.sanitized_text or answer_text

        answer = GroundedAnswer(
            query=query,
            answer=final_answer_text,
            citations=citations,
            web_citations=(),
            confidence=confidence,
            requires_human_review=review.requires_human_review,
            review_level=review.level,
            review_reasons=review.reasons,
            caveats=tuple(dict.fromkeys(caveats)),  # de-dup, preserve order
        )
        self._audit_answer(actor, redacted_q, answer, Decision.ALLOWED)
        return answer

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _anchor(
        self,
        answer_text: str,
        citations: tuple[Citation, ...],
        passages: list[RetrievedPassage],
    ) -> tuple[Citation, ...]:
        """Resolve each citation to the layout block its claim came from (B2 locator).

        Reads the document's stored chunks through the ``CitationStorePort``, scoped to
        the admitted passage's actual owner tenant (including the shared/global empty
        tenant), and hands them to the pure resolver. With no
        store bound, an unreadable store, or a document whose blocks are below the match
        floor, the citation is returned unchanged at page level: anchor precision is a
        refinement of provenance, never a precondition for it.
        """
        if self._citation_store is None or not citations:
            return citations
        owner_tenants = {passage.citation.document_id: passage.tenant for passage in passages}
        chunks_by_document: dict[str, list[Any]] = {}
        for citation in citations:
            if citation.document_id in chunks_by_document:
                continue
            try:
                chunks_by_document[citation.document_id] = list(
                    self._citation_store.get(
                        citation.document_id,
                        owner_tenants.get(citation.document_id, ""),
                    )
                )
            except Exception:  # noqa: BLE001 - store trouble degrades to page-level
                chunks_by_document[citation.document_id] = []
        return anchor_citations(
            answer_text,
            citations,
            chunks_by_document,
            self._citation_policy.anchor_match_floor,
        )

    def _self_critique(
        self,
        query: str,
        answer_text: str,
        passage_block: str,
        prior_confidence: float,
    ) -> tuple[float, list[str]]:
        """Second LLM pass auditing groundedness; returns (confidence, caveats)."""
        request = g.build_llm_request(
            system_instruction=SELF_CRITIQUE_SYSTEM,
            user_content=SELF_CRITIQUE_USER.format(
                query=query, answer=answer_text, passages=passage_block
            ),
            model=None,
            response_schema=_CRITIQUE_SCHEMA,
            temperature=0.0,
        )
        try:
            response = self._llm.generate(request)
        except Exception:  # noqa: BLE001 - critique failure must not drop the answer
            return prior_confidence, []
        g.maybe_record_usage(self._tracer, response)

        parsed = g.parse_structured(response)
        if not parsed:
            return prior_confidence, []

        critic_conf = _clamp(parsed.get("confidence", prior_confidence))
        grounded = bool(parsed.get("grounded", True))
        caveats = g.as_str_list(parsed.get("caveats"))

        # Take the more conservative of the two confidence signals.
        confidence = min(prior_confidence, critic_conf)
        if not grounded:
            confidence = min(confidence, 0.4)
            if not caveats:
                caveats = ["Self-critique flagged unsupported claims; review required."]
        return confidence, caveats

    # ------------------------------------------------------------------ #
    # Degraded / blocked answers
    # ------------------------------------------------------------------ #
    def _no_grounding(
        self,
        query: str,
        redacted_q: str,
        actor: str,
        access_denied: bool,
    ) -> GroundedAnswer:
        """Handle "no ACL-admitted passage": raise (reference) or degrade (configured).

        Either way the ESCALATED audit record is written FIRST, so the refusal is on the
        record before the error propagates and a caller cannot make an ungrounded
        request disappear by catching the exception.
        """
        caveat = _ACCESS_DENIED_CAVEAT if access_denied else _EMPTY_RETRIEVAL_CAVEAT
        answer = GroundedAnswer(
            query=query,
            answer=(
                "I could not find permitted passages to ground an answer to this "
                "query. Please refine the query or check your access entitlements."
            ),
            citations=(),
            confidence=0.0,
            requires_human_review=True,
            review_level=ReviewLevel.ENHANCED,
            review_reasons=(REASON_UNGROUNDED,),
            caveats=(caveat,),
        )
        self._audit_answer(actor, redacted_q, answer, Decision.ESCALATED)
        if self._answer_policy.empty_retrieval_raises:
            # B2: never hand back an answer no citation supports.
            raise RetrievalEmptyError(caveat)
        return answer

    def _blocked_answer(
        self,
        query: str,
        redacted_q: str,
        actor: str,
        verdict: GuardrailVerdict,
    ) -> GroundedAnswer:
        """Blocked answer when the guardrail denies the output."""
        reason = verdict.reason or "blocked by guardrail"
        answer = GroundedAnswer(
            query=query,
            answer=(
                "This request was blocked by the safety guardrail and was not "
                f"answered (output: {reason})."
            ),
            citations=(),
            confidence=0.0,
            requires_human_review=True,
            review_level=ReviewLevel.ENHANCED,
            review_reasons=self._review.evaluate(0.0, set(), blocked=True).reasons,
            caveats=(_BLOCKED_CAVEAT,),
        )
        self._audit_answer(actor, redacted_q, answer, Decision.BLOCKED)
        return answer

    # ------------------------------------------------------------------ #
    # Audit
    # ------------------------------------------------------------------ #
    def _audit_search(
        self,
        actor: str,
        redacted_query: str,
        citations: tuple[Citation, ...],
        decision: Decision,
        denied: bool = False,
    ) -> None:
        event = AuditEvent(
            action="search",
            actor=actor,
            decision=decision,
            redacted_prompt=redacted_query,
            redacted_response=f"{len(citations)} passage(s)",
            citations=citations,
            metadata={
                "n_passages": str(len(citations)),
                "access_denied": str(denied).lower(),
            },
        )
        self._safe_record(event)

    def _audit_answer(
        self,
        actor: str,
        redacted_query: str,
        answer: GroundedAnswer,
        decision: Decision,
    ) -> None:
        event = AuditEvent(
            action="answer",
            actor=actor,
            decision=decision,
            redacted_prompt=redacted_query,
            redacted_response=answer.answer,
            citations=answer.citations,
            metadata={
                "confidence": f"{answer.confidence:.3f}",
                "requires_human_review": str(answer.requires_human_review).lower(),
                "review_level": str(answer.review_level),
                "review_reasons": ",".join(answer.review_reasons),
                "n_citations": str(len(answer.citations)),
                "n_anchored": str(sum(1 for c in answer.citations if c.anchor)),
            },
        )
        self._safe_record(event)

    def _safe_record(self, event: AuditEvent) -> None:
        """Persist the governed decision before it is returned to the caller.

        Audit is a consequential control, not optional telemetry.  Propagating a sink
        failure prevents an allowed/blocked result from escaping without its immutable
        evidence record and lets the API map the operational failure explicitly.
        """
        self._audit.record(event)
