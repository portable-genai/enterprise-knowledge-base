"""Domain models for the Enterprise Knowledge Base (system A2) : the **vertical** half.

This module is the heart of the hexagon. It has **no dependency on Google Cloud,
ADK, FastAPI, or any framework** : only the Python standard library. Every adapter
(GCP, remote-platform, or on-prem placeholder) speaks in terms of these types, which
is what lets the managed-service stack be swapped for an on-premise one without
touching domain logic (General Principle P-02, "no vendor lock-in / ports & adapters").

A2 is the shared, **ACL-aware governed RAG** over the bank corpus: it ingests
documents (with ACL tags, residency and freshness metadata), redacts PII at the
boundary, indexes them for retrieval, and serves **ACL-filtered, cited** passages
(and optionally a grounded synthesized answer). It is a horizontal platform service
that every other agent queries.

**Kernel vs vertical (A7).** The vertical-neutral machinery : provenance, the LLM
envelope, safety verdicts, the maker-checker scale, the audit record and the eval report
: lives in :mod:`enterprise_kb.domain.kernel`, which a fork keeps unchanged. THIS module
owns the artifact models a fork rewrites: documents, chunks, ACL tags, retrieval
passages, freshness records and :class:`GroundedAnswer`. The kernel names are re-exported
below so ``from enterprise_kb.domain.models import Citation`` keeps working; the import
direction is one-way (models -> kernel) and asserted by
``tests/contract/test_kernel_boundary.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from hex_service_kit import StrEnum

# Kernel re-exports: the vertical-neutral machinery, imported once and re-published so
# every existing import site keeps working across the A7 boundary.
from .kernel import (
    AuditEvent,
    BlockKind,
    BoundingBox,
    Citation,
    Decision,
    Direction,
    EvalMetricResult,
    EvalReport,
    GuardrailCategory,
    GuardrailFinding,
    GuardrailVerdict,
    LlmMessage,
    LlmRequest,
    LlmResponse,
    RedactionFinding,
    RedactionResult,
    ReviewLevel,
    ReviewOutcome,
    ThinkingLevel,
    TokenUsage,
    WebCitation,
    utcnow,
)

__all__ = [
    # kernel (vertical-neutral, re-exported)
    "AuditEvent",
    "BlockKind",
    "BoundingBox",
    "Citation",
    "Decision",
    "Direction",
    "EvalMetricResult",
    "EvalReport",
    "GuardrailCategory",
    "GuardrailFinding",
    "GuardrailVerdict",
    "LlmMessage",
    "LlmRequest",
    "LlmResponse",
    "RedactionFinding",
    "RedactionResult",
    "ReviewLevel",
    "ReviewOutcome",
    "ThinkingLevel",
    "TokenUsage",
    "WebCitation",
    "utcnow",
    # vertical (this module)
    "AclPrincipal",
    "AclTag",
    "AgentCard",
    "AgentSkill",
    "Document",
    "DocumentChunk",
    "FreshnessRecord",
    "FreshnessStatus",
    "GroundedAnswer",
    "IngestResult",
    "KbQuery",
    "MemoryItem",
    "PrincipalKind",
    "RetrievedPassage",
    "Session",
    "SourceSystem",
    "ToolSpec",
]


# --------------------------------------------------------------------------- #
# Access control : ACL principals and tags
# --------------------------------------------------------------------------- #
class PrincipalKind(StrEnum):
    """The kind of identity an ACL principal represents."""

    USER = "USER"
    GROUP = "GROUP"
    SERVICE = "SERVICE"


@dataclass(frozen=True, slots=True)
class AclPrincipal:
    """A caller identity whose visible ACL tags gate what the KB returns.

    A principal (a user, a directory group, or a service account) is resolved by the
    AccessControlPort into the set of ``AclTag`` labels it is permitted to see. The
    domain then filters retrieved passages to that set, never the adapter (P-09).
    """

    id: str  # e.g. "user:jane@bank.test", "group:retail-credit", "svc:kyc-agent"
    kind: PrincipalKind = PrincipalKind.USER


@dataclass(frozen=True, slots=True)
class AclTag:
    """A single access-control label attached to a document, chunk, or passage.

    A passage is admissible for a query only if **every** one of its ``acl_tags`` is in
    the set the caller's principals resolve to (all-of / subset matching), so a passage
    tagged ``{dept:risk, classification:restricted}`` requires the caller to hold *both*.
    Tags are opaque strings (e.g. ``"dept:retail"``, ``"classification:internal"``) so
    the labelling scheme stays a deployment concern, not a domain one.
    """

    label: str


# --------------------------------------------------------------------------- #
# Documents and chunks
# --------------------------------------------------------------------------- #
class SourceSystem(StrEnum):
    """Where an ingested document originated (for provenance and routing)."""

    CONFLUENCE = "confluence"
    SHAREPOINT = "sharepoint"
    GCS = "gcs"
    FILE_UPLOAD = "file_upload"
    POLICY_PORTAL = "policy_portal"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class Document:
    """A source document tracked in the corpus, with ACL and residency metadata."""

    id: str  # stable slug / document id, e.g. "policy-aml-onboarding-v3"
    title: str
    uri: str
    source_system: SourceSystem = SourceSystem.OTHER
    acl_tags: tuple[AclTag, ...] = ()  # labels gating who may retrieve this document
    tenant: str = ""  # owning tenant partition; "" means shared/global (visible to all)
    residency_region: str = "asia-southeast1"  # single-region corpus (P-03)
    version: str = "unknown"
    fetched_at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True, slots=True)
class DocumentChunk:
    """One retrievable chunk of a parsed document (the portable parser output unit).

    Layout-aware parsing makes a chunk a **layout block**, not a slice of page text, so
    the chunk carries the anchor that locates it: ``anchor`` is the stable
    ``p{page}#b{index}`` id from :mod:`enterprise_kb.domain.layout`, ``bbox`` the
    normalised rectangle a reviewer highlights, and ``kind`` the block role (paragraph,
    table, heading, list). All three are optional: a chunk from a corpus ingested before
    layout parsing still has only ``page``, and every consumer must keep working with
    that : an unanchored chunk (and the citation built from it) is valid, degraded
    provenance, never an error.
    """

    document_id: str
    ordinal: int  # 0-based position of the chunk within the document
    text: str
    page: int | None = None
    embedding_ref: str | None = None  # opaque pointer to the vector for this chunk
    anchor: str | None = None  # stable layout-block locator, e.g. "p3#b2"
    bbox: BoundingBox | None = None  # normalised page rectangle of the block
    kind: BlockKind = BlockKind.PARAGRAPH  # layout role of the block (B5)


# --------------------------------------------------------------------------- #
# Retrieval and citation
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class KbQuery:
    """An inbound search/answer request against the knowledge base.

    ``allowed_tags`` are the already-resolved ACL labels and ``tenant`` is the verified
    tenant partition. Managed retrieval may use both for candidate pushdown; the domain
    still repeats the all-of tag and tenant admission checks after retrieval.
    ``filters`` are structured retrieval filters resolved by the adapter (e.g.
    ``{"source_system": "confluence"}``).
    """

    text: str
    top_k: int = 10
    allowed_tags: tuple[str, ...] = ()
    tenant: str = ""
    filters: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RetrievedPassage:
    """An ACL-admitted passage returned for a query.

    ``acl_tags`` are the labels that admitted this passage : carried through so the
    caller (and the audit log) can see *why* it was visible to the principal. ``tenant``
    is the owning tenant partition (``""`` means shared/global); the domain drops a
    passage whose tenant does not match the caller's (multi-tenant isolation).
    """

    text: str
    citation: Citation
    score: float = 0.0
    acl_tags: tuple[AclTag, ...] = ()
    tenant: str = ""


# --------------------------------------------------------------------------- #
# Runtime, session and memory
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class Session:
    id: str
    user_id: str
    case_id: str | None = None
    created_at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True, slots=True)
class MemoryItem:
    id: str
    content: str
    scope: str = "user"  # "user" | "case" | "global"
    created_at: datetime = field(default_factory=utcnow)


# --------------------------------------------------------------------------- #
# Governance : A3 Agent Registry and Governance concerns (A2A AgentCard)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class AgentSkill:
    id: str
    name: str
    description: str


@dataclass(frozen=True, slots=True)
class AgentCard:
    """Portable future A2A card model; no managed endpoint advertises it today."""

    name: str
    description: str
    url: str
    version: str
    skills: tuple[AgentSkill, ...] = ()
    provider: str = "enterprise-knowledge-base"


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """A governed, least-privilege tool exposed to the agent (typically via MCP)."""

    name: str
    description: str
    input_schema: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Corpus freshness : residency + freshness ledger
# --------------------------------------------------------------------------- #
class FreshnessStatus(StrEnum):
    FRESH = "fresh"
    EXPIRED = "expired"
    MISSING = "missing"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class FreshnessRecord:
    """One document's residency + freshness state in the ledger.

    The ledger is the home of P-03 (residency: ``residency_region`` is recorded per
    document) and the freshness window (``expires_at``). A record is fresh while its
    status is FRESH and the current time is before ``expires_at``.
    """

    document_id: str
    residency_region: str
    fetched_at: datetime
    expires_at: datetime
    tenant: str = ""  # owning tenant partition; "" is shared/global corpus (visible to all)
    version: str = "unknown"
    checksum: str = ""
    status: FreshnessStatus = FreshnessStatus.FRESH
    source_authority: str = "direct"  # "registry" may be tombstoned on registry removal

    def is_fresh(self, now: datetime | None = None) -> bool:
        now = now or utcnow()
        return self.status == FreshnessStatus.FRESH and now < self.expires_at


@dataclass(frozen=True, slots=True)
class IngestResult:
    """The outcome of ingesting one document into the governed store.

    ``chunk_anchors`` are the anchored chunks the parser produced for this document. The
    ingestion adapter parses; the domain :class:`IngestionService` is what persists them
    through the :class:`~enterprise_kb.ports.citations.CitationStorePort`, so the store
    is a port the domain drives rather than a second backend each adapter has to know
    about. An adapter that does not parse layout returns ``()`` and the pipeline keeps
    working at page level.
    """

    document_id: str
    chunks: int = 0
    ok: bool = True
    redaction_findings: tuple[RedactionFinding, ...] = ()
    detail: str = ""
    chunk_anchors: tuple[DocumentChunk, ...] = ()


# --------------------------------------------------------------------------- #
# Top-level KB outputs (the artifacts A2 serves)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class GroundedAnswer:
    """A cited answer assembled from ACL-filtered retrieved passages.

    The answer is LLM-synthesised but never beyond the retrieved set: citations are
    preserved from the passages that grounded it.

    Maker-checker (P-06) is a floor, not a switch: ``requires_human_review`` defaults to
    ``True`` and the service never produces it ``False``, because a synthesised answer
    over governed content is a consequential output. Hard signals (low confidence, a
    sensitive ACL classification, an ungrounded or blocked answer) only raise
    ``review_level`` from STANDARD to ENHANCED; ``review_reasons`` records why, for the
    audit trail and for the Hrz7 review queue.
    """

    query: str
    answer: str
    citations: tuple[Citation, ...] = ()
    web_citations: tuple[WebCitation, ...] = ()
    confidence: float = 0.0
    requires_human_review: bool = True
    review_level: ReviewLevel = ReviewLevel.STANDARD
    review_reasons: tuple[str, ...] = ()
    caveats: tuple[str, ...] = ()
