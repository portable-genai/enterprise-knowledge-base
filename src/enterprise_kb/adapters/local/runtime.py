"""Local agent-runtime adapter (AgentRuntimePort) : in-process reasoning loop.

The ``local`` profile's stand-in for **Agent Runtime** (the hosted reasoning engine):
rather than calling a deployed endpoint, it assembles the domain
:class:`KnowledgeBaseService` from the other local adapters and answers in-process. Under
``local`` the platform client runs the app itself (a laptop runs one app, not the whole
platform). SDK-free and unconditional.

The session's ``user_id`` is passed through as the caller principal so the in-process
answer is ACL-filtered exactly like a served request: an unknown principal resolves to no
tags (access-denied) and gets a caveated, review-flagged answer.
"""

from __future__ import annotations

from ...config import Settings
from ...domain.models import GroundedAnswer, Session


class LocalAgentRuntimeAdapter:
    """In-process agent runtime: answers via the local KnowledgeBaseService."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._service = None

    def _kb_service(self):  # type: ignore[no-untyped-def]
        if self._service is None:
            from ...domain.kb_service import KnowledgeBaseService
            from .access_control import LocalAccessControlAdapter
            from .audit import LocalAppendOnlyAuditAdapter
            from .citation_store import LocalSqliteCitationStore
            from .guardrail import LocalHeuristicGuardrailAdapter
            from .llm import LocalDeterministicLLMAdapter
            from .redaction import LocalRegexRedactionAdapter
            from .retrieval import LocalFtsRetrievalAdapter
            from .tracer import LocalNoopTracerAdapter

            self._service = KnowledgeBaseService(
                retrieval=LocalFtsRetrievalAdapter(self._settings),
                access_control=LocalAccessControlAdapter(self._settings),
                guardrail=LocalHeuristicGuardrailAdapter(self._settings),
                redaction=LocalRegexRedactionAdapter(self._settings),
                llm=LocalDeterministicLLMAdapter(self._settings),
                tracer=LocalNoopTracerAdapter(self._settings),
                audit=LocalAppendOnlyAuditAdapter(self._settings),
                citation_store=LocalSqliteCitationStore(self._settings),
                citation_policy=self._settings.policy.citation_policy(),
            )
        return self._service

    def query(self, session: Session, message: str) -> GroundedAnswer:
        return self._kb_service().answer(
            message, actor=session.user_id, acl_principals=(session.user_id,)
        )

    def health(self) -> bool:
        return True
