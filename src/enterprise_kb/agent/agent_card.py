"""A2A AgentCard for the A2 Enterprise Knowledge Base (A3 Registry & Governance).

This builds a future discovery document in the same minimal A2A shape the
``agent-registry`` service stores and serves. The current managed deployment neither
serves nor publishes it because no verified-context A2A transport exists;
:func:`agent_card_document` is a pure design seam, not a readiness signal.

The card advertises exactly the two read-only skills A2 offers (search_kb and
answer_grounded), mirroring the ADK FunctionTools so a peer agent or the registry sees one
consistent capability surface. Corpus mutation is a separately governed pipeline workload.

This module is pure (domain models only) and imports without ADK or any Google Cloud SDK
installed (SPEC §4).
"""

from __future__ import annotations

from typing import Any

from ..config import Settings
from ..domain.models import AgentCard, AgentSkill

# Skill ids are the stable, machine-facing capability names; they intentionally match the
# FunctionTool callables in ``agent.tools`` so the card and the tool surface stay in
# lockstep.
SKILLS: tuple[AgentSkill, ...] = (
    AgentSkill(
        id="search_kb",
        name="ACL-aware search",
        description=(
            "Return ACL-filtered, page-cited passages from the bank corpus for a query, "
            "scoped to the caller's access entitlements."
        ),
    ),
    AgentSkill(
        id="answer_grounded",
        name="Grounded answer",
        description=(
            "Synthesise a cited answer over the caller's permitted passages, never "
            "beyond the retrieved set, with a maker-checker review flag (P-06)."
        ),
    ),
)

_DESCRIPTION = (
    "A2 Enterprise Knowledge Base : the shared, ACL-aware governed RAG over the bank "
    "corpus. Serves ACL-filtered, cited passages and grounded answers and governs "
    "document ingestion (redact-before-index, single-region residency, freshness). "
    "Built ports-and-adapters with AlloyDB retrieval and portable document parsing; "
    "managed Agent Runtime registration stays fail-closed pending verified invocation context."
)


def build_agent_card(settings: Settings) -> AgentCard:
    """Construct the A2A :class:`AgentCard` for this service."""
    base_url = _resolve_url(settings)
    return AgentCard(
        name="enterprise-knowledge-base",
        description=_DESCRIPTION,
        url=base_url,
        version="0.1.0",
        skills=SKILLS,
        provider="enterprise-knowledge-base",
    )


def agent_card_document(settings: Settings) -> dict[str, Any]:
    """Return a future A2A document; no managed endpoint serves it today."""
    from ..domain.serialization import to_jsonable

    return to_jsonable(build_agent_card(settings))


def _resolve_url(settings: Settings) -> str:
    """Best-effort public URL for the card, region-pinned to asia-southeast1."""
    resource = settings.agent_engine.resource_name
    if resource:
        return f"https://aiplatform.googleapis.com/v1/{resource}"
    return "https://enterprise-knowledge-base.hrz.internal/a2a"
