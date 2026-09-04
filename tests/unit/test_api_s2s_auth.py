"""S2S auth tests: authenticate the *calling service* beside the end-user Principal.

These exercise the FastAPI app end to end with the ``local`` profile, proving the shared
S2S contract (plan-hrz-s2s-auth, CD1) on enterprise-knowledge-base's governed data plane:

* fail-OPEN when ``KB_S2S_TOKEN`` is unset (the offline default, so the CI gate runs with
  zero secrets), fail-CLOSED (401) when it is set and the bearer is missing or wrong;
* ``/healthz`` stays open either way;
* the S2S bearer and the end-user identity are INDEPENDENT seams: a valid service token
  does not bypass identity (an unknown persona is still 401), and the verified persona is
  still the audit actor when both credentials are present.

Like ``test_api_identity``, ``deps.get_container`` is monkeypatched to an in-memory
container built from the real ``local`` adapters, so nothing touches the filesystem or GCP.
"""

from __future__ import annotations

from collections.abc import Iterator
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
from enterprise_kb.api.security import _TOKEN_ENV
from enterprise_kb.config import LocalSettings, Settings
from enterprise_kb.domain.models import AuditEvent


def _settings() -> Settings:
    return Settings(
        profile="local",
        local=LocalSettings(db_path=":memory:", audit_path=":memory:", ledger_path=":memory:"),
    )


class _RecordingAudit(LocalAppendOnlyAuditAdapter):
    """Local append-only audit that also keeps the AuditEvent objects for assertions."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.events: list[AuditEvent] = []

    def record(self, event: AuditEvent) -> None:
        self.events.append(event)
        super().record(event)


class _Container:
    """Minimal in-memory Container: the ports the KB read surface consumes, all local."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        retrieval = LocalFtsRetrievalAdapter(settings)
        retrieval.seed(list(sample_docs.SAMPLE_PASSAGES))
        self.retrieval = retrieval
        self.access_control = LocalAccessControlAdapter(settings)
        self.guardrail = LocalHeuristicGuardrailAdapter(settings)
        self.redaction = LocalRegexRedactionAdapter(settings)
        self.llm = LocalDeterministicLLMAdapter(settings)
        self.tracer = LocalNoopTracerAdapter(settings)
        self.audit = _RecordingAudit(settings)
        self.identity = LocalPersonaIdentityAdapter(settings)
        self.citation_store = LocalSqliteCitationStore(settings)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    container = _Container(_settings())

    def _get_container() -> Any:
        return container

    # get_container is lru_cached; inject the in-memory container instead of mutating env.
    monkeypatch.setattr(deps, "get_container", _get_container)
    monkeypatch.setattr(app_module.deps, "get_container", _get_container)
    return TestClient(app_module.app, client=LOOPBACK_PEER)


@pytest.fixture
def token_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    monkeypatch.setenv(_TOKEN_ENV, "s3cret-service-token")
    yield "s3cret-service-token"


def _search(client: TestClient, headers: dict[str, str] | None = None):
    return client.post(
        "/v1/search",
        json={"query": sample_docs.SAMPLE_QUERY, "acl_principals": []},
        headers=headers,
    )


def test_no_token_configured_is_open_loopback_dev(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # KB_S2S_TOKEN unset: the offline default. The default persona resolves identity and the
    # guarded route stays writable/readable with no service token (zero-secret CI gate).
    monkeypatch.delenv(_TOKEN_ENV, raising=False)
    assert _search(client).status_code == 200


def test_healthz_never_requires_a_token(client: TestClient, token_env: str) -> None:
    assert client.get("/healthz").status_code == 200


def test_missing_token_is_401_when_enforced(client: TestClient, token_env: str) -> None:
    assert _search(client).status_code == 401


def test_wrong_token_is_401_when_enforced(client: TestClient, token_env: str) -> None:
    assert _search(client, {"Authorization": "Bearer nope"}).status_code == 401


def test_correct_token_is_accepted(client: TestClient, token_env: str) -> None:
    assert _search(client, {"Authorization": f"Bearer {token_env}"}).status_code == 200


def test_service_token_does_not_bypass_identity(client: TestClient, token_env: str) -> None:
    # A valid service token authenticates the CALLER, not the USER: an unknown end-user
    # persona is still a 401. The two auth seams are independent.
    resp = _search(
        client,
        {"Authorization": f"Bearer {token_env}", "X-Dev-Persona": "does-not-exist"},
    )
    assert resp.status_code == 401


def test_service_and_identity_coexist(client: TestClient, token_env: str) -> None:
    # Both credentials present: the service bearer passes S2S AND the verified persona
    # (auditor) becomes the audit actor. The end user still travels via its own header.
    resp = _search(
        client,
        {"Authorization": f"Bearer {token_env}", "X-Dev-Persona": "auditor"},
    )
    assert resp.status_code == 200
    events = app_module.deps.get_container().audit.events
    assert events, "the search must have written an audit event"
    assert all(e.actor == "demo.auditor@bank.example" for e in events)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
