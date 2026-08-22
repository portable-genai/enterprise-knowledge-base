"""Pytest fixtures: the ``local`` adapters (seeded) + assembled domain services.

The unit suite is driven by the **real** ``local`` adapter family
(``src/enterprise_kb/adapters/local``) rather than bespoke in-memory fakes, so the
offline implementation lives in exactly one place and the tests exercise the same code
the offline CLI runs. Every adapter constructs with a single ``Settings`` (the adapter
convention) pointed at ``:memory:`` SQLite, and the retrieval index is seeded with the
synthetic ``tests/fixtures/sample_docs`` corpus for determinism.

A few fixtures wrap the local adapter in a thin **recording** subclass that captures
call arguments for assertions (``.calls`` / ``.requests`` / ``.spans`` / ``.events``).
These add no behaviour: every method delegates to the real local adapter, so the
in-memory implementation is still the one under ``adapters/local``. The recorders are
the test instrumentation the previous bespoke fakes used to bundle.

The local LLM is schema-driven (it reads ``request.response_schema`` and returns a JSON
object whose keys match it, including ``used_document_ids`` recovered from the rendered
``[document_id p.N]`` passage headers), so it stays correct whatever field names the
services declare. The two blocked-path tests use :class:`BlockingGuardrail`, a thin local
guardrail subclass that force-blocks deterministically (the one behaviour that cannot be
driven by input text alone, so it stays as a seedable local variant per the brief).
"""

from __future__ import annotations

import importlib
from typing import Any

import pytest

from enterprise_kb.adapters.local.access_control import LocalAccessControlAdapter
from enterprise_kb.adapters.local.audit import LocalAppendOnlyAuditAdapter
from enterprise_kb.adapters.local.evaluation import LocalOfflineEvalAdapter
from enterprise_kb.adapters.local.grounding import LocalDisabledGroundingAdapter
from enterprise_kb.adapters.local.guardrail import LocalHeuristicGuardrailAdapter
from enterprise_kb.adapters.local.ingestion import LocalIngestionAdapter
from enterprise_kb.adapters.local.ledger import LocalLedgerAdapter
from enterprise_kb.adapters.local.llm import LocalDeterministicLLMAdapter
from enterprise_kb.adapters.local.memory import LocalMemoryAdapter
from enterprise_kb.adapters.local.redaction import LocalRegexRedactionAdapter
from enterprise_kb.adapters.local.registry import LocalRegistryAdapter
from enterprise_kb.adapters.local.retrieval import LocalFtsRetrievalAdapter
from enterprise_kb.adapters.local.runtime import LocalAgentRuntimeAdapter
from enterprise_kb.adapters.local.session import LocalSessionAdapter
from enterprise_kb.adapters.local.tool_catalog import LocalToolCatalogAdapter
from enterprise_kb.adapters.local.tracer import LocalNoopTracerAdapter
from enterprise_kb.config import LocalSettings, Settings
from enterprise_kb.domain.models import (
    AuditEvent,
    Direction,
    GuardrailCategory,
    GuardrailFinding,
    GuardrailVerdict,
    KbQuery,
    LlmRequest,
    LlmResponse,
    RetrievedPassage,
)
from tests.fixtures import sample_docs

#: A loopback peer for every ``TestClient``. The app-object exposure guard refuses the
#: unauthenticated ``local`` posture to any other peer, and TestClient's DEFAULT peer is the
#: literal host ``"testclient"``, which is not a loopback address and is refused with a 503.
LOOPBACK_PEER = ("127.0.0.1", 50000)


def _settings() -> Settings:
    """Settings whose local stores are ephemeral in-memory SQLite (deterministic)."""
    return Settings(
        profile="local",
        local=LocalSettings(db_path=":memory:", audit_path=":memory:", ledger_path=":memory:"),
    )


# --------------------------------------------------------------------------- #
# Recording wrappers : thin subclasses of the local adapters that capture call
# arguments for assertions. Every method delegates to the real local adapter.
# --------------------------------------------------------------------------- #
class FakeRetrieval(LocalFtsRetrievalAdapter):
    """Local FTS5 retrieval (seeded) that records the queries it received.

    Kept named ``FakeRetrieval`` so the existing unit tests import it unchanged; it is a
    real local adapter underneath, seeded with the synthetic corpus. Pass ``passages=[]``
    to simulate an empty corpus.

    The unit suite asserts the *domain ACL-admission* decision (P-09), not FTS ranking, so
    ``retrieve`` returns the seeded candidate set (top_k-sliced) and lets the domain filter
    by resolved tags : the candidate-recall stage is what the local and managed AlloyDB FTS
    adapters own, and is exercised directly by the contract test. The real
    FTS5 query path stays available as :meth:`fts_retrieve`.
    """

    def __init__(
        self, settings: Settings | None = None, passages: list[RetrievedPassage] | None = None
    ) -> None:
        super().__init__(settings or _settings())
        candidates = list(sample_docs.SAMPLE_PASSAGES) if passages is None else list(passages)
        # Re-seed the in-memory index with the requested corpus (empty for the empty case).
        self.seed(candidates)
        self._candidates = candidates
        self.calls: list[KbQuery] = []

    def retrieve(self, query: KbQuery) -> list[RetrievedPassage]:
        self.calls.append(query)
        return list(self._candidates)[: query.top_k]

    def fts_retrieve(self, query: KbQuery) -> list[RetrievedPassage]:
        """The underlying FTS5 BM25 query path (used by the end-to-end / contract checks)."""
        return super().retrieve(query)


class FakeAccessControl(LocalAccessControlAdapter):
    """Local access-control directory that records the principals it resolved.

    Defaults to the synthetic ``sample_docs.PRINCIPAL_TAGS`` directory (which mirrors the
    built-in local seed), so a retail principal resolves to retail+internal and an
    unknown principal resolves to no tags (access-denied, fail-closed).
    """

    def __init__(
        self, settings: Settings | None = None, mapping: dict[str, set[str]] | None = None
    ) -> None:
        super().__init__(settings or _settings())
        if mapping is not None:
            self._directory = {k: set(v) for k, v in mapping.items()}
        else:
            self._directory = {k: set(v) for k, v in sample_docs.PRINCIPAL_TAGS.items()}
        self.calls: list[list[str]] = []

    def resolve(self, principals: list[str], tenant: str) -> set[str]:
        self.calls.append(list(principals))
        return super().resolve(principals, tenant)


class RecordingLLM(LocalDeterministicLLMAdapter):
    """Local deterministic LLM that records the requests it received."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.requests: list[LlmRequest] = []
        self.classify_calls: list[tuple[str, list[str]]] = []

    def generate(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        return super().generate(request)

    def classify(self, text: str, labels: list[str]) -> str:
        self.classify_calls.append((text, labels))
        return super().classify(text, labels)


class RecordingRedaction(LocalRegexRedactionAdapter):
    """Local regex redaction that records the raw text it was asked to redact."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.calls: list[str] = []

    def redact(self, text: str):  # type: ignore[no-untyped-def]
        self.calls.append(text)
        return super().redact(text)


class RecordingTracer(LocalNoopTracerAdapter):
    """Local no-op tracer that records the span names it opened."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.spans: list[str] = []

    def span(self, name: str, **attributes: str):  # type: ignore[no-untyped-def]
        self.spans.append(name)
        return super().span(name, **attributes)


class RecordingGuardrail(LocalHeuristicGuardrailAdapter):
    """Local heuristic guardrail that records the (text, direction) screen calls.

    Behaviour is the real heuristic: benign text passes, malicious text (e.g.
    ``sample_docs.MALICIOUS_QUERY``) is blocked. Only the recording is added.
    """

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.calls: list[tuple[str, Direction]] = []

    def screen(self, text: str, direction: Direction):  # type: ignore[no-untyped-def]
        self.calls.append((text, direction))
        return super().screen(text, direction)


class BlockingGuardrail(LocalHeuristicGuardrailAdapter):
    """Local guardrail variant that force-blocks a chosen direction, regardless of text.

    The heuristic guardrail blocks on malicious *content*; a couple of pipeline tests
    instead need to assert the *blocked path* for otherwise-benign text (a blocked output
    on a clean answer, a blocked input on a clean document). Forcing the verdict is a
    behaviour input alone cannot express, so this stays a thin, deterministic local
    subclass (per the build brief) rather than a separate bespoke fake.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        block_input: bool = True,
        block_output: bool = False,
    ) -> None:
        super().__init__(settings or _settings())
        self.block_input = block_input
        self.block_output = block_output
        self.calls: list[tuple[str, Direction]] = []

    def screen(self, text: str, direction: Direction) -> GuardrailVerdict:
        self.calls.append((text, direction))
        forced = (direction is Direction.INPUT and self.block_input) or (
            direction is Direction.OUTPUT and self.block_output
        )
        if forced:
            return GuardrailVerdict(
                allowed=False,
                direction=direction,
                findings=(
                    GuardrailFinding(
                        category=GuardrailCategory.PROMPT_INJECTION,
                        confidence="high",
                        detail="prompt injection detected",
                    ),
                ),
                sanitized_text=None,
                reason="blocked by guardrail",
            )
        # Otherwise defer to the real heuristic (still blocks genuinely malicious text).
        return super().screen(text, direction)


class RecordingAudit(LocalAppendOnlyAuditAdapter):
    """Local append-only audit that also keeps the AuditEvent objects for assertions."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.events: list[AuditEvent] = []

    def record(self, event: AuditEvent) -> None:
        self.events.append(event)
        super().record(event)


class RecordingIngestion(LocalIngestionAdapter):
    """Local ingestion adapter that records the (document, content, mime) triples indexed."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.ingested: list[tuple[Any, bytes, str]] = []
        self.deleted: list[str] = []

    def ingest(self, document, content, mime_type):  # type: ignore[no-untyped-def]
        self.ingested.append((document, content, mime_type))
        return super().ingest(document, content, mime_type)

    def delete(self, document_id: str, tenant: str = "") -> None:
        self.deleted.append(document_id)
        super().delete(document_id, tenant)


# --------------------------------------------------------------------------- #
# Service / policy resolvers : locate the domain classes wherever they live.
# --------------------------------------------------------------------------- #
_SERVICE_MODULE_CANDIDATES = (
    "enterprise_kb.domain.kb_service",
    "enterprise_kb.domain.ingestion_service",
    "enterprise_kb.domain.services",
    "enterprise_kb.domain.orchestration",
)
# KbReviewPolicy lives in hitl.py, FreshnessPolicy in freshness_policy.py.
_POLICY_MODULE_CANDIDATES = (
    "enterprise_kb.domain.hitl",
    "enterprise_kb.domain.freshness_policy",
    "enterprise_kb.domain.policies",
    "enterprise_kb.domain.services",
)


def _resolve(symbol: str, candidates: tuple[str, ...]) -> Any:
    last: Exception | None = None
    for mod_name in candidates:
        try:
            module = importlib.import_module(mod_name)
        except ModuleNotFoundError as exc:  # pragma: no cover - layout fallback
            last = exc
            continue
        obj = getattr(module, symbol, None)
        if obj is not None:
            return obj
    raise ImportError(f"Could not locate domain symbol {symbol!r} in any of {candidates}") from last


def load_service(name: str) -> Any:
    return _resolve(name, _SERVICE_MODULE_CANDIDATES)


def load_policy(name: str) -> Any:
    return _resolve(name, _POLICY_MODULE_CANDIDATES)


# --------------------------------------------------------------------------- #
# Pytest fixtures : construct the (seeded) local adapters.
# --------------------------------------------------------------------------- #
@pytest.fixture
def retrieval() -> FakeRetrieval:
    return FakeRetrieval(_settings())


@pytest.fixture
def empty_retrieval() -> FakeRetrieval:
    return FakeRetrieval(_settings(), passages=[])


@pytest.fixture
def access_control() -> FakeAccessControl:
    return FakeAccessControl(_settings())


@pytest.fixture
def llm() -> RecordingLLM:
    return RecordingLLM(_settings())


@pytest.fixture
def grounding() -> LocalDisabledGroundingAdapter:
    return LocalDisabledGroundingAdapter(_settings())


@pytest.fixture
def guardrail() -> RecordingGuardrail:
    return RecordingGuardrail(_settings())


@pytest.fixture
def blocking_guardrail() -> BlockingGuardrail:
    return BlockingGuardrail(_settings())


@pytest.fixture
def redaction() -> RecordingRedaction:
    return RecordingRedaction(_settings())


@pytest.fixture
def tracer() -> RecordingTracer:
    return RecordingTracer(_settings())


@pytest.fixture
def audit() -> RecordingAudit:
    return RecordingAudit(_settings())


@pytest.fixture
def session() -> LocalSessionAdapter:
    return LocalSessionAdapter(_settings())


@pytest.fixture
def memory() -> LocalMemoryAdapter:
    return LocalMemoryAdapter(_settings())


@pytest.fixture
def agent_runtime() -> LocalAgentRuntimeAdapter:
    return LocalAgentRuntimeAdapter(_settings())


@pytest.fixture
def evaluation() -> LocalOfflineEvalAdapter:
    return LocalOfflineEvalAdapter(_settings())


@pytest.fixture
def registry() -> LocalRegistryAdapter:
    return LocalRegistryAdapter(_settings())


@pytest.fixture
def tool_catalog() -> LocalToolCatalogAdapter:
    return LocalToolCatalogAdapter(_settings())


@pytest.fixture
def ledger() -> LocalLedgerAdapter:
    return LocalLedgerAdapter(_settings())


@pytest.fixture
def ingestion() -> RecordingIngestion:
    return RecordingIngestion(_settings())


# Direction is re-exported for unit tests that import it from the conftest namespace.
__all__ = ["Direction"]


@pytest.fixture
def kb_service(retrieval, access_control, guardrail, redaction, llm, tracer, audit):
    """KnowledgeBaseService(retrieval, access_control, guardrail, redaction, llm, tracer, audit)."""
    cls = load_service("KnowledgeBaseService")
    return cls(retrieval, access_control, guardrail, redaction, llm, tracer, audit)


@pytest.fixture
def ingestion_service(ingestion, redaction, guardrail, ledger, tracer, audit):
    """IngestionService(ingestion, redaction, guardrail, ledger, tracer, audit)."""
    cls = load_service("IngestionService")
    return cls(ingestion, redaction, guardrail, ledger, tracer, audit)
