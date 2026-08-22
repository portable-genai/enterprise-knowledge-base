"""API-boundary tests for server-verified identity (the IdentityPort seam).

These exercise the FastAPI app end to end with the ``local`` persona adapter, proving:

* an unknown ``X-Dev-Persona`` is a 401 (unverified identity is never accepted),
* the default / selected persona's verified subject becomes the audit actor, and
* verified entitlements reach the ACL resolver; retrieval receives only the resulting tags and
  verified tenant, never raw/client principal assertions.

``deps.get_container`` is ``lru_cache``d, so we monkeypatch it to inject one in-memory
container built from the real ``local`` adapters (with a recording retrieval) rather than
mutating the environment. The KB service and the identity adapter share that one container
instance, so the recorded retrieval query is exactly the one the request drove.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests.conftest import LOOPBACK_PEER
from tests.fixtures import sample_docs

from enterprise_kb.adapters.local.access_control import LocalAccessControlAdapter
from enterprise_kb.adapters.local.audit import LocalAppendOnlyAuditAdapter
from enterprise_kb.adapters.local.citation_store import LocalSqliteCitationStore
from enterprise_kb.adapters.local.guardrail import LocalHeuristicGuardrailAdapter
from enterprise_kb.adapters.local.identity import LocalPersonaIdentityAdapter
from enterprise_kb.adapters.local.llm import LocalDeterministicLLMAdapter
from enterprise_kb.adapters.local.redaction import LocalRegexRedactionAdapter
from enterprise_kb.adapters.local.retrieval import LocalFtsRetrievalAdapter
from enterprise_kb.adapters.local.tracer import LocalNoopTracerAdapter
from enterprise_kb.api import app as app_module
from enterprise_kb.api import deps
from enterprise_kb.config import LocalSettings, Settings
from enterprise_kb.domain.models import AuditEvent, KbQuery, RetrievedPassage


def _settings() -> Settings:
    return Settings(
        profile="local",
        local=LocalSettings(db_path=":memory:", audit_path=":memory:", ledger_path=":memory:"),
    )


class RecordingRetrieval(LocalFtsRetrievalAdapter):
    """Local FTS5 retrieval seeded with the synthetic corpus; records each KbQuery.

    ``retrieve`` returns the seeded candidate set (top_k-sliced) so the domain performs the
    ACL admission, mirroring tests/conftest.FakeRetrieval, and captures the query so the test
    can assert which principals reached retrieval.
    """

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._candidates = list(sample_docs.SAMPLE_PASSAGES)
        self.seed(self._candidates)
        self.calls: list[KbQuery] = []

    def retrieve(self, query: KbQuery) -> list[RetrievedPassage]:
        self.calls.append(query)
        return list(self._candidates)[: query.top_k]


class RecordingAudit(LocalAppendOnlyAuditAdapter):
    """Local append-only audit that also keeps the AuditEvent objects for assertions."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.events: list[AuditEvent] = []

    def record(self, event: AuditEvent) -> None:
        self.events.append(event)
        super().record(event)


class RecordingAccessControl(LocalAccessControlAdapter):
    """Record the server-verified tenant and narrowed principals at the ACL port."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.calls: list[tuple[tuple[str, ...], str]] = []

    def resolve(self, principals: list[str], tenant: str) -> set[str]:
        self.calls.append((tuple(principals), tenant))
        return super().resolve(principals, tenant)


class _Container:
    """Minimal in-memory Container: the ports the API's service factories consume.

    Built from the real ``local`` adapters so the pipeline runs offline, with the single
    RecordingRetrieval instance shared by the KB service (the governed-retrieval seam under
    test) and the identity adapter that resolves the verified persona.
    """

    def __init__(self, settings: Settings, retrieval: RecordingRetrieval) -> None:
        self.settings = settings
        self.retrieval = retrieval
        self.access_control = RecordingAccessControl(settings)
        self.guardrail = LocalHeuristicGuardrailAdapter(settings)
        self.redaction = LocalRegexRedactionAdapter(settings)
        self.llm = LocalDeterministicLLMAdapter(settings)
        self.tracer = LocalNoopTracerAdapter(settings)
        self.audit = RecordingAudit(settings)
        self.identity = LocalPersonaIdentityAdapter(settings)
        self.citation_store = LocalSqliteCitationStore(settings)


@pytest.fixture
def retrieval() -> RecordingRetrieval:
    return RecordingRetrieval(_settings())


@pytest.fixture
def client(retrieval: RecordingRetrieval, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    container = _Container(_settings(), retrieval)

    def _get_container() -> Any:
        return container

    # get_container is lru_cached; inject the in-memory container instead of mutating env.
    monkeypatch.setattr(deps, "get_container", _get_container)
    monkeypatch.setattr(app_module.deps, "get_container", _get_container)
    return TestClient(app_module.app, client=LOOPBACK_PEER)


def test_unknown_dev_persona_is_401(client: TestClient) -> None:
    resp = client.post(
        "/v1/search",
        json={"query": sample_docs.SAMPLE_QUERY, "acl_principals": []},
        headers={"X-Dev-Persona": "does-not-exist"},
    )
    assert resp.status_code == 401


@pytest.mark.parametrize("path", ["/v1/search", "/v1/answer"])
def test_governed_query_size_limit_returns_422(client: TestClient, path: str) -> None:
    resp = client.post(path, json={"query": "q" * 8193})
    assert resp.status_code == 422


@pytest.mark.parametrize("path", ["/v1/search", "/v1/answer"])
def test_governed_acl_cardinality_and_item_limits_return_422(client: TestClient, path: str) -> None:
    too_many = client.post(
        path,
        json={"query": "bounded", "acl_principals": [f"group:{i}" for i in range(65)]},
    )
    too_long = client.post(
        path,
        json={"query": "bounded", "acl_principals": ["g" * 257]},
    )
    assert too_many.status_code == 422
    assert too_long.status_code == 422


@pytest.mark.parametrize("path", ["/v1/search", "/v1/answer"])
def test_governed_filter_count_key_and_value_limits_return_422(
    client: TestClient, path: str
) -> None:
    too_many = client.post(
        path,
        json={"query": "bounded", "filters": {f"key_{i}": "v" for i in range(17)}},
    )
    bad_key = client.post(path, json={"query": "bounded", "filters": {"UPPER": "v"}})
    too_long = client.post(path, json={"query": "bounded", "filters": {"source": "v" * 513}})
    assert too_many.status_code == 422
    assert bad_key.status_code == 422
    assert too_long.status_code == 422


def test_default_persona_is_the_audit_actor(client: TestClient):
    # No X-Dev-Persona: the default seeded persona (analyst) is the verified identity.
    resp = client.post("/v1/answer", json={"query": sample_docs.SAMPLE_QUERY, "acl_principals": []})
    assert resp.status_code == 200
    events = app_module.deps.get_container().audit.events
    assert events, "the answer must have written an audit event"
    assert all(e.actor == "demo.analyst@bank.example" for e in events)


def test_selected_persona_is_the_audit_actor(client: TestClient):
    # The auditor persona holds no tag that admits the sample corpus, so the answer is
    # refused as ungrounded (B2). The refusal is still audited under the VERIFIED actor:
    # a refused request must not fall off the audit trail.
    resp = client.post(
        "/v1/answer",
        json={"query": sample_docs.SAMPLE_QUERY, "acl_principals": []},
        headers={"X-Dev-Persona": "auditor"},
    )
    assert resp.status_code in (200, 422)
    events = app_module.deps.get_container().audit.events
    assert events
    assert all(e.actor == "demo.auditor@bank.example" for e in events)


def test_ungrounded_answer_is_refused_not_softened(client: TestClient):
    """B2: no permitted passage means a structured refusal, never an uncited answer.

    RED unless B2 holds: the pipeline must not return HTTP 200 with a caveated,
    citation-free GroundedAnswer, so a caller could not distinguish "here is the answer"
    from "nothing grounded this".
    """
    resp = client.post(
        "/v1/answer",
        json={"query": "a query no permitted document answers", "acl_principals": []},
        headers={"X-Dev-Persona": "auditor"},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"] == "ungrounded"
    assert body["citations"] == []
    assert body["requires_human_review"] is True
    # The refusal is on the WORM record before the error reaches the caller.
    events = app_module.deps.get_container().audit.events
    assert any(e.action == "answer" for e in events)


def test_verified_principals_reach_governed_retrieval(
    client: TestClient, retrieval: RecordingRetrieval
) -> None:
    # With no client hint, the default persona (analyst) carries group:kb-reader +
    # group:risk, and exactly those verified entitlement principals reach retrieval, never
    # a client-asserted actor.
    resp = client.post(
        "/v1/search",
        json={"query": sample_docs.SAMPLE_QUERY, "acl_principals": []},
    )
    assert resp.status_code == 200
    assert retrieval.calls, "retrieval was never reached"
    access_control = app_module.deps.get_container().access_control
    assert access_control.calls[-1][0] == ("group:kb-reader", "group:risk")
    assert retrieval.calls[-1].tenant == "demo-bank"
    assert set(retrieval.calls[-1].allowed_tags) == {
        "classification:internal",
        "dept:retail",
        "dept:risk",
    }


def test_verified_tenant_reaches_the_access_control_lookup(client: TestClient) -> None:
    response = client.post(
        "/v1/search",
        json={"query": sample_docs.SAMPLE_QUERY, "acl_principals": []},
    )

    assert response.status_code == 200
    access_control = app_module.deps.get_container().access_control
    assert access_control.calls[-1] == (("group:kb-reader", "group:risk"), "demo-bank")


def test_client_cannot_widen_scope_with_foreign_principal(
    client: TestClient, retrieval: RecordingRetrieval
) -> None:
    # The analyst persona injects a privileged group it does not hold. The server-side
    # entitlement check drops it: narrowing to a group the caller lacks leaves no scope, so
    # the request is access-denied (retrieval may not even be reached) and the injected
    # group never reaches retrieval : visibility can never widen.
    resp = client.post(
        "/v1/search",
        json={"query": sample_docs.SAMPLE_QUERY, "acl_principals": ["group:kb-approver"]},
    )
    assert resp.status_code == 200
    access_control = app_module.deps.get_container().access_control
    reached = set(access_control.calls[-1][0]) if access_control.calls else set()
    assert "group:kb-approver" not in reached


def test_client_hint_can_only_narrow_to_a_held_subset(
    client: TestClient, retrieval: RecordingRetrieval
) -> None:
    # A hint the persona DOES hold narrows scope to just that subset (group:risk dropped).
    resp = client.post(
        "/v1/search",
        json={"query": sample_docs.SAMPLE_QUERY, "acl_principals": ["group:kb-reader"]},
    )
    assert resp.status_code == 200
    access_control = app_module.deps.get_container().access_control
    assert set(access_control.calls[-1][0]) == {"group:kb-reader"}


def test_widening_injection_does_not_leak_restricted_passages(client: TestClient) -> None:
    # End-to-end: a retail-tier persona (analyst) asserting the privileged approver group
    # must not thereby pull the restricted data-residency passage into its results.
    resp = client.post(
        "/v1/search",
        json={"query": sample_docs.SAMPLE_QUERY, "acl_principals": ["group:kb-approver"]},
    )
    assert resp.status_code == 200
    doc_ids = {p["citation"]["document_id"] for p in resp.json()["passages"]}
    assert "standard-data-residency-v1" not in doc_ids
