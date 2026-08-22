"""FastAPI dependency wiring for the A2 Enterprise Knowledge Base.

This module builds a single, process-wide :class:`~enterprise_kb.config.Container` (the
ports-and-adapters registry) and assembles the two orchestration services from the
Container's port instances. The Container is created lazily on first access so importing
this module : and therefore the FastAPI app : never touches Google Cloud: a unit test or
the on-prem profile can import the API with no GCP SDK installed.

Each factory is a FastAPI ``Depends`` provider. Services take *explicit port instances*
in their constructors (SPEC §5), so the wiring here is the single place that knows which
ports each service needs.
"""

from __future__ import annotations

from functools import lru_cache

from ..config import Container, Settings, build_container
from ..domain.freshness_policy import FreshnessPolicy
from ..domain.hitl import KbReviewPolicy
from ..domain.services import IngestionService, KnowledgeBaseService


@lru_cache(maxsize=1)
def get_container() -> Container:
    """Return the process-wide Container, building it on first use."""
    return build_container(Settings.load())


def get_settings() -> Settings:
    """Convenience accessor for the active settings (region, profile, corpus TTL...)."""
    return get_container().settings


# --------------------------------------------------------------------------- #
# Service factories : assemble each service from the Container's ports.
# Constructor argument order mirrors SPEC §5 exactly.
# --------------------------------------------------------------------------- #


def get_kb_service() -> KnowledgeBaseService:
    """KnowledgeBaseService(retrieval, access_control, guardrail, redaction, llm, tracer, audit)."""
    return build_kb_service(get_container())


def get_ingestion_service() -> IngestionService:
    """IngestionService(ingestion, redaction, guardrail, ledger, tracer, audit)."""
    return build_ingestion_service(get_container())


def build_kb_service(container: Container) -> KnowledgeBaseService:
    """Assemble a :class:`KnowledgeBaseService` from an explicit Container.

    The maker-checker thresholds and the grounding rules come from the bank-owned
    ``policy:`` settings section (B4), so no engine carries a tunable constant.
    """
    policy = container.settings.policy
    return KnowledgeBaseService(
        retrieval=container.retrieval,
        access_control=container.access_control,
        guardrail=container.guardrail,
        redaction=container.redaction,
        llm=container.llm,
        tracer=container.tracer,
        audit=container.audit,
        review_policy=KbReviewPolicy.from_policy(policy),
        answer_policy=policy.answer_policy(),
        citation_store=container.citation_store,
        citation_policy=policy.citation_policy(),
    )


def build_ingestion_service(container: Container) -> IngestionService:
    """Assemble an :class:`IngestionService` from an explicit Container.

    The freshness window and the residency region come from the bank-owned ``policy:``
    settings section (B4), so the ingest path and the refresh job share one source.
    """
    return IngestionService(
        ingestion=container.ingestion,
        redaction=container.redaction,
        guardrail=container.guardrail,
        ledger=container.ledger,
        tracer=container.tracer,
        audit=container.audit,
        freshness_policy=FreshnessPolicy.from_policy(container.settings.policy),
        citation_store=container.citation_store,
    )


def create_app():  # noqa: ANN201 - FastAPI app; annotated where imported
    """App factory used by uvicorn's ``--factory`` mode and the CLI ``serve`` command."""
    from .app import app

    return app
