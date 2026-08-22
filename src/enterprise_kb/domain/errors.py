"""Domain exceptions for the Enterprise Knowledge Base (system A2).

Pure-Python exception hierarchy raised by the orchestration services. The domain
layer never imports Google Cloud, ADK, or any framework : these errors let callers
(API, CLI, the Agent Runtime adapter) react to domain-level failures without coupling
to any vendor SDK error type.
"""

from __future__ import annotations


class KnowledgeBaseError(Exception):
    """Base class for all domain-level errors raised by A2 services."""


class GuardrailBlockedError(KnowledgeBaseError):
    """Raised/flagged when the A1 guardrail blocks an input or output.

    The search/answer pipeline generally degrades gracefully (returns a blocked
    ``GroundedAnswer`` with ``requires_human_review=True`` or an empty passage list)
    rather than raising; the ingest path raises this so a blocked unsafe document is
    never indexed.
    """


class RetrievalEmptyError(KnowledgeBaseError):
    """Raised when retrieval returns no ACL-admitted passages for a query.

    The grounded-answer path returns a low-confidence, caveated ``GroundedAnswer``
    flagged for human review rather than raising; callers that must be grounded can
    treat an empty result as a hard error.
    """


class AccessDeniedError(KnowledgeBaseError):
    """Raised when a principal resolves to no ACL tags at all.

    A caller with no admissible tags can see nothing in the corpus; surfacing this
    explicitly (rather than returning silently empty results) lets the API distinguish
    "no access" from "no matching documents".
    """


class IngestionError(KnowledgeBaseError):
    """Raised when a document cannot be parsed, redacted, or indexed.

    The pipeline records a FAILED freshness record before surfacing this so the next
    refresh pass retries the document.
    """
