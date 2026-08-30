"""Pydantic v2 request/response models for the A2 Enterprise Knowledge Base API.

These schemas mirror the frozen domain dataclasses in
:mod:`enterprise_kb.domain.models` one-for-one, so the HTTP boundary is a thin, typed
projection of the domain : the React/Next.js UI, the CLI and the sibling services
consume exactly these shapes. Each response model exposes a ``from_domain`` classmethod
that builds itself from the corresponding domain object, using
:func:`domain.serialization.to_jsonable` where a nested structure is easiest to project
verbatim (enums become their ``.value`` strings).

Nothing here imports Google Cloud, ADK, or any adapter: the API layer depends only on
the domain models, the ports, and the orchestration services.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, Field, StringConstraints

from ..domain import models as m
from ..domain.serialization import to_jsonable

# --------------------------------------------------------------------------- #
# Shared projections
# --------------------------------------------------------------------------- #


class BoundingBoxModel(BaseModel):
    """Normalised page rectangle of an anchored citation (mirror of BoundingBox)."""

    x0: float = 0.0
    y0: float = 0.0
    x1: float = 0.0
    y1: float = 0.0


class CitationModel(BaseModel):
    """Provenance attached to a passage / grounded claim (mirror of Citation).

    ``anchor`` and ``bbox`` are the anchor-level locator: the layout block the claim was
    resolved to and the rectangle a viewer highlights. Both stay optional on the wire, so
    a client written against the page-level contract keeps working and a citation that
    could not be resolved to a block is still a valid citation.
    """

    document_id: str
    title: str
    uri: str
    version: str = "unknown"
    page: int | None = None
    snippet: str = ""
    score: float | None = None
    anchor: str | None = None
    bbox: BoundingBoxModel | None = None

    @classmethod
    def from_domain(cls, citation: m.Citation) -> CitationModel:
        return cls(**to_jsonable(citation))


class AclTagModel(BaseModel):
    label: str

    @classmethod
    def from_domain(cls, tag: m.AclTag) -> AclTagModel:
        return cls(label=tag.label)


class WebCitationModel(BaseModel):
    title: str
    url: str
    snippet: str = ""

    @classmethod
    def from_domain(cls, citation: m.WebCitation) -> WebCitationModel:
        return cls(**to_jsonable(citation))


# --------------------------------------------------------------------------- #
# Requests
# --------------------------------------------------------------------------- #

QueryText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8192)]
PrincipalHint = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)
]
FilterName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_]*$",
    ),
]
FilterValue = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=512)]


class SearchRequest(BaseModel):
    """Inbound ACL-aware search request.

    There is no ``actor`` field: the audit actor is the server-verified
    :class:`~enterprise_kb.domain.identity.Principal` resolved by the IdentityPort, never
    a client-asserted value. ``acl_principals`` is an optional scope-DOWN hint: it is
    entitlement-checked against the verified principal's own groups server-side, so it can
    only narrow visibility to a subset and never widen it. The tenant partition likewise
    comes from the verified principal, never the request body.
    """

    query: QueryText = Field(..., description="The bounded query to search the corpus for.")
    top_k: int = Field(default=10, ge=1, le=50)
    acl_principals: list[PrincipalHint] = Field(
        default_factory=list,
        max_length=64,
        description="Optional scope-down hint: caller principal ids to narrow to. Ids the "
        "verified principal does not hold are ignored (can only narrow, never widen).",
    )
    filters: dict[FilterName, FilterValue] | None = Field(
        default=None, max_length=16, description="Optional bounded structured retrieval filters."
    )


class AnswerRequest(BaseModel):
    """Inbound grounded-answer request (audit actor comes from the verified Principal).

    ``acl_principals`` is the same entitlement-checked scope-down hint as
    :class:`SearchRequest`: it can only narrow the verified principal's scope, never widen.
    """

    query: QueryText
    acl_principals: list[PrincipalHint] = Field(default_factory=list, max_length=64)
    filters: dict[FilterName, FilterValue] | None = Field(default=None, max_length=16)


class IngestRequest(BaseModel):
    """Inbound document-ingest request (content base64-encoded)."""

    document_id: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    uri: str = Field(default="")
    source_system: str = Field(default="other")
    acl_tags: list[str] = Field(default_factory=list)
    version: str = Field(default="unknown")
    content_b64: str = Field(..., description="Base64-encoded document bytes.")
    mime_type: str = Field(default="application/pdf")


# --------------------------------------------------------------------------- #
# Responses
# --------------------------------------------------------------------------- #


class RetrievedPassageModel(BaseModel):
    """An ACL-admitted passage (mirror of RetrievedPassage)."""

    text: str
    citation: CitationModel
    score: float = 0.0
    acl_tags: list[AclTagModel] = Field(default_factory=list)

    @classmethod
    def from_domain(cls, passage: m.RetrievedPassage) -> RetrievedPassageModel:
        return cls(
            text=passage.text,
            citation=CitationModel.from_domain(passage.citation),
            score=passage.score,
            acl_tags=[AclTagModel.from_domain(t) for t in passage.acl_tags],
        )


class SearchResponse(BaseModel):
    """ACL-filtered passages for a query."""

    passages: list[RetrievedPassageModel] = Field(default_factory=list)

    @classmethod
    def from_domain(cls, passages: list[m.RetrievedPassage]) -> SearchResponse:
        return cls(passages=[RetrievedPassageModel.from_domain(p) for p in passages])


class AnswerResponse(BaseModel):
    """Grounded answer with provenance (mirror of GroundedAnswer)."""

    query: str
    answer: str
    citations: list[CitationModel] = Field(default_factory=list)
    web_citations: list[WebCitationModel] = Field(default_factory=list)
    confidence: float = 0.0
    # Maker-checker (P-06) is a floor: the service never emits this False for a
    # synthesised answer. `review_level` carries the escalation (standard | enhanced).
    requires_human_review: bool = True
    review_level: str = "standard"
    review_reasons: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)

    @classmethod
    def from_domain(cls, answer: m.GroundedAnswer) -> AnswerResponse:
        return cls(
            query=answer.query,
            answer=answer.answer,
            citations=[CitationModel.from_domain(c) for c in answer.citations],
            web_citations=[WebCitationModel.from_domain(c) for c in answer.web_citations],
            confidence=answer.confidence,
            requires_human_review=answer.requires_human_review,
            review_level=str(answer.review_level),
            review_reasons=list(answer.review_reasons),
            caveats=list(answer.caveats),
        )


class RedactionFindingModel(BaseModel):
    info_type: str
    count: int = 1

    @classmethod
    def from_domain(cls, finding: m.RedactionFinding) -> RedactionFindingModel:
        return cls(info_type=finding.info_type, count=finding.count)


class IngestResponse(BaseModel):
    """Outcome of ingesting a document (mirror of IngestResult)."""

    document_id: str
    chunks: int = 0
    ok: bool = True
    redaction_findings: list[RedactionFindingModel] = Field(default_factory=list)
    detail: str = ""

    @classmethod
    def from_domain(cls, result: m.IngestResult) -> IngestResponse:
        return cls(
            document_id=result.document_id,
            chunks=result.chunks,
            ok=result.ok,
            redaction_findings=[
                RedactionFindingModel.from_domain(f) for f in result.redaction_findings
            ],
            detail=result.detail,
        )


# --------------------------------------------------------------------------- #
# Corpus & health
# --------------------------------------------------------------------------- #


class FreshnessRecordModel(BaseModel):
    """One document's freshness + residency state in the ledger."""

    document_id: str
    residency_region: str
    fetched_at: str
    expires_at: str
    version: str = "unknown"
    checksum: str = ""
    status: str = "fresh"
    source_authority: str = "direct"

    @classmethod
    def from_domain(cls, record: m.FreshnessRecord) -> FreshnessRecordModel:
        return cls(**to_jsonable(record))


class CorpusStatusResponse(BaseModel):
    """Summary of the freshness ledger (ledger.all() rolled up)."""

    total: int = 0
    fresh: int = 0
    expired: int = 0
    missing: int = 0
    failed: int = 0
    ttl_days: int = 7
    records: list[FreshnessRecordModel] = Field(default_factory=list)

    @classmethod
    def from_records(cls, records: list[m.FreshnessRecord], ttl_days: int) -> CorpusStatusResponse:
        counts: dict[str, int] = {s.value: 0 for s in m.FreshnessStatus}
        now = m.utcnow()
        for record in records:
            effective = record.status.value
            if record.status == m.FreshnessStatus.FRESH and not record.is_fresh(now):
                effective = m.FreshnessStatus.EXPIRED.value
            counts[effective] = counts.get(effective, 0) + 1
        return cls(
            total=len(records),
            fresh=counts.get(m.FreshnessStatus.FRESH.value, 0),
            expired=counts.get(m.FreshnessStatus.EXPIRED.value, 0),
            missing=counts.get(m.FreshnessStatus.MISSING.value, 0),
            failed=counts.get(m.FreshnessStatus.FAILED.value, 0),
            ttl_days=ttl_days,
            records=[FreshnessRecordModel.from_domain(r) for r in records],
        )


class HealthResponse(BaseModel):
    """Liveness/readiness of the knowledge base and its active profile."""

    status: str = "ok"
    profile: str = "local"
    #: Provenance the UI banner states on every page: where the runtime sits and which
    #: model answers. Derived server-side so the console never guesses (org decision,
    #: 2026-08-30).
    runtime: str = "local"  # "gcp" | "local"
    generator_model: str = "deterministic-offline-stub"
    region: str = "asia-southeast1"


# --------------------------------------------------------------------------- #
# Governance : A2A AgentCard projection
# --------------------------------------------------------------------------- #


class AgentSkillModel(BaseModel):
    id: str
    name: str
    description: str


class AgentCardModel(BaseModel):
    """Future A2A projection; retained for portability but not exposed by the API."""

    name: str
    description: str
    url: str
    version: str
    provider: str = "enterprise-knowledge-base"
    skills: list[AgentSkillModel] = Field(default_factory=list)

    @classmethod
    def from_domain(cls, card: m.AgentCard) -> AgentCardModel:
        data: dict[str, Any] = to_jsonable(card)
        return cls(**data)
