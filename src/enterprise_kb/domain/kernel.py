"""Domain **kernel**: the vertical-neutral machinery a fork never edits (A7).

The domain is split in two, and the split is the fork boundary:

* **kernel** (this module) : machinery that is true of every governed GenAI service in
  the catalog, whatever the vertical. Provenance (:class:`Citation`, :class:`WebCitation`),
  the LLM envelope (:class:`LlmRequest` / :class:`LlmResponse` / :class:`TokenUsage`),
  safety verdicts (:class:`GuardrailVerdict`, :class:`RedactionResult`), the maker-checker
  scale (:class:`ReviewLevel`, :class:`ReviewOutcome`), the audit record
  (:class:`AuditEvent`) and the eval report (:class:`EvalReport`). An adopter forking A2
  for another vertical keeps this module byte-for-byte.
* **vertical** (:mod:`enterprise_kb.domain.models`) : the artifact models of *this*
  product : documents, chunks, ACL tags, retrieval passages, freshness records and the
  :class:`~enterprise_kb.domain.models.GroundedAnswer` A2 serves. A fork rewrites these.

The dependency direction is one-way and enforced by
``tests/contract/test_kernel_boundary.py``: ``models`` imports from ``kernel``, never the
reverse, and ``kernel`` imports nothing from this package at all. ``models`` re-exports
every kernel name, so ``from enterprise_kb.domain.models import Citation`` keeps working
and no call site had to change when the boundary was drawn (ARCHITECTURE 1.1).

Standard library only: no Google Cloud, ADK, FastAPI or pydantic imports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from hex_service_kit import StrEnum
from hex_service_kit.observability import TokenUsage


def utcnow() -> datetime:
    """Timezone-aware UTC now : the single clock the domain uses."""
    return datetime.now(UTC)


# --------------------------------------------------------------------------- #
# Provenance
# --------------------------------------------------------------------------- #
class BlockKind(StrEnum):
    """The layout role of a parsed block, and of the chunk projected from it (B5).

    Vertical-neutral: every document parser in the catalog reports the same four roles,
    so the vocabulary lives with the provenance machinery rather than with this
    product's artifact models.
    """

    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST = "list"
    TABLE = "table"


@dataclass(frozen=True, slots=True)
class BoundingBox:
    """A normalised rectangle on a page: the visual half of an anchor.

    Coordinates are page-relative floats in ``[0.0, 1.0]`` with the origin at the top
    left, so a viewer can highlight the region without knowing the page's pixel size and
    the value survives a re-render at another resolution. ``x0/y0`` is the top-left
    corner and ``x1/y1`` the bottom-right; a degenerate box (zero width or height) is
    allowed and simply highlights nothing.
    """

    x0: float = 0.0
    y0: float = 0.0
    x1: float = 0.0
    y1: float = 0.0

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.x0, self.y0, self.x1, self.y1)


@dataclass(frozen=True, slots=True)
class Citation:
    """Provenance attached to every retrieved passage and grounded claim.

    Page-level citation is a hard requirement: an answer assembled from the corpus
    must point to the exact source document, version and page so a reviewer can verify
    it. This shape is reused (minus regulator taxonomy) from the C1 citation contract.

    ``anchor`` and ``bbox`` are the **anchor-level** refinement of that locator: the
    stable id of the layout block the claim was resolved to (``p3#b2``) and its
    normalised bounding box on the page, so a reviewer lands on the paragraph or table
    cell rather than the page. Both are optional and default to ``None``: a citation
    that carries only a page is still a valid citation (a corpus ingested before
    layout-aware parsing, or a block a claim could not be resolved to above the
    configured match floor), never an error.
    """

    document_id: str
    title: str
    uri: str
    version: str = "unknown"
    page: int | None = None
    snippet: str = ""
    score: float | None = None
    anchor: str | None = None
    bbox: BoundingBox | None = None


@dataclass(frozen=True, slots=True)
class WebCitation:
    """Provenance for a public-web grounded fact (secondary, cross-border)."""

    title: str
    url: str
    snippet: str = ""


# --------------------------------------------------------------------------- #
# Generation (LLM envelope)
# --------------------------------------------------------------------------- #
class ThinkingLevel(StrEnum):
    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True, slots=True)
class LlmMessage:
    role: str  # "user" | "model" | "system"
    content: str


@dataclass(frozen=True, slots=True)
class LlmRequest:
    messages: tuple[LlmMessage, ...]
    system_instruction: str | None = None
    model: str | None = None  # None => adapter default from config
    thinking: ThinkingLevel = ThinkingLevel.MEDIUM
    temperature: float = 0.0  # omitted at a call site means this value; it must not sample
    max_output_tokens: int = 4096
    response_schema: dict | None = None  # JSON schema for structured output


# ``TokenUsage`` is NOT declared here. It is imported from ``hex_service_kit.observability``
# at the top of this module. Three ``int`` fields defaulting to zero, redeclared byte-identically
# in every repository that needs them, is a shared value type that is not actually being
# shared. Redeclaring it is what lets the copies drift, so the class body is
# gone rather than kept "for the kernel's purity". The commons version is the same frozen,
# slotted, three-field dataclass, so every construction and every field read below is unchanged,
# and ``tests/contract/test_port_parity.py`` asserts object identity so a future copy fails
# loudly instead of quietly diverging.


@dataclass(frozen=True, slots=True)
class LlmResponse:
    text: str
    usage: TokenUsage = field(default_factory=TokenUsage)
    model: str = ""
    web_citations: tuple[WebCitation, ...] = ()
    raw: dict | None = None


# --------------------------------------------------------------------------- #
# Safety verdicts (guardrail + PII redaction)
# --------------------------------------------------------------------------- #
class GuardrailCategory(StrEnum):
    PROMPT_INJECTION = "prompt_injection"
    JAILBREAK = "jailbreak"
    SENSITIVE_DATA = "sensitive_data"
    MALICIOUS_URL = "malicious_url"
    HATE = "hate"
    HARASSMENT = "harassment"
    SEXUAL = "sexual"
    DANGEROUS = "dangerous"
    OTHER = "other"


class Direction(StrEnum):
    INPUT = "input"
    OUTPUT = "output"


@dataclass(frozen=True, slots=True)
class GuardrailFinding:
    category: GuardrailCategory
    confidence: str  # e.g. "low" | "medium" | "high"
    detail: str = ""


@dataclass(frozen=True, slots=True)
class GuardrailVerdict:
    allowed: bool
    direction: Direction
    findings: tuple[GuardrailFinding, ...] = ()
    # Text after any inline sanitisation the guardrail applied (may equal input).
    sanitized_text: str | None = None
    reason: str = ""


@dataclass(frozen=True, slots=True)
class RedactionFinding:
    info_type: str  # e.g. "PERSON_NAME", "SG_NRIC_FIN", "CREDIT_CARD_NUMBER"
    count: int = 1


@dataclass(frozen=True, slots=True)
class RedactionResult:
    text: str  # de-identified text safe to send to the model / index / audit log
    findings: tuple[RedactionFinding, ...] = ()

    @property
    def redacted(self) -> bool:
        return bool(self.findings)


# --------------------------------------------------------------------------- #
# Maker-checker scale (P-06)
# --------------------------------------------------------------------------- #
class ReviewLevel(StrEnum):
    """How much human scrutiny a consequential output must receive.

    Maker-checker is a floor, not a switch: every synthesised output is reviewed, and a
    hard signal (low confidence, a sensitive classification, an ungrounded answer) only
    ever RAISES the bar from STANDARD to ENHANCED. There is no level below STANDARD.
    """

    STANDARD = "standard"
    ENHANCED = "enhanced"


@dataclass(frozen=True, slots=True)
class ReviewOutcome:
    """The maker-checker verdict for one consequential output.

    ``requires_human_review`` is the P-06 floor and is never produced ``False`` by the
    policy; ``level`` carries the escalation and ``reasons`` the auditable justification.
    """

    requires_human_review: bool
    level: ReviewLevel = ReviewLevel.STANDARD
    reasons: tuple[str, ...] = ()

    @property
    def escalated(self) -> bool:
        return self.level is ReviewLevel.ENHANCED


# --------------------------------------------------------------------------- #
# Audit record
# --------------------------------------------------------------------------- #
class Decision(StrEnum):
    ALLOWED = "allowed"
    BLOCKED = "blocked"
    ESCALATED = "escalated"  # routed to a human (maker-checker)


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """An immutable, WORM-stored record of one KB interaction.

    Prompt and response are stored **already redacted** (P-04): PII is removed at the
    boundary before it is ever written to the audit sink or a trace span.
    """

    action: str  # "search" | "answer" | "ingest" | "delete"
    actor: str  # authenticated user / service identity
    decision: Decision
    redacted_prompt: str
    redacted_response: str
    citations: tuple[Citation, ...] = ()
    resource: str = "enterprise-knowledge-base"
    trace_id: str | None = None
    timestamp: datetime = field(default_factory=utcnow)
    metadata: dict[str, str] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Evaluation report
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class EvalMetricResult:
    metric: str  # "retrieval_recall" | "acl_correctness" | "citation_accuracy" | "safety"
    score: float
    threshold: float
    passed: bool


@dataclass(frozen=True, slots=True)
class EvalReport:
    dataset: str
    results: tuple[EvalMetricResult, ...]
    n_examples: int = 0

    @property
    def passed(self) -> bool:
        """True only when every metric passed AND there was something to evaluate.

        The naive ``all(...)`` is a fail-open. ``all(())`` is vacuously True, so a report
        that scored nothing reported PASSED, and ``eval/run_eval.py`` exits 0 on this
        property: an evaluation with no results certified a promotion. Both extra guards
        earn their place. An empty ``results`` means no metric was ever computed, and
        ``n_examples == 0`` means no example was scored even if a metric row reached here
        some other way.
        """
        return self.n_examples > 0 and bool(self.results) and all(r.passed for r in self.results)


#: Every name the vertical module re-exports. The contract test reads this list, so a
#: new kernel type is either exported here or it is not part of the kernel contract.
KERNEL_EXPORTS: tuple[str, ...] = (
    "utcnow",
    "Citation",
    "WebCitation",
    "ThinkingLevel",
    "LlmMessage",
    "LlmRequest",
    "TokenUsage",
    "LlmResponse",
    "GuardrailCategory",
    "Direction",
    "GuardrailFinding",
    "GuardrailVerdict",
    "RedactionFinding",
    "RedactionResult",
    "ReviewLevel",
    "ReviewOutcome",
    "Decision",
    "AuditEvent",
    "EvalMetricResult",
    "EvalReport",
)
