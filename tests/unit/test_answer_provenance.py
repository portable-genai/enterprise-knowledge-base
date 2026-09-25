"""The service half of the model pills: which model ANSWERED, and whether it searched.

The console shows two pills at the top right: the model that answered the last request, and
``Search`` when that answer used an online search tool (owner decision, 2026-09-23). Both come
from response headers the kit emits (``install_answer_provenance`` in ``api/app.py``) for
whatever the model adapters NOTED as they called. Before a request is answered the pill shows
``generator_model`` from ``/healthz``, so that value must be the model the bound adapter calls,
never one a configuration flag names while the adapter calls another.

The console calls this service cross-origin when it runs standalone, and a browser hides every
response header not listed in ``Access-Control-Expose-Headers``, so that listing is pinned too.
No API route reaches an online search tool today (the Gemini ``google_search`` grounding adapter
is bound but no route calls it), so the ``X-Search-Used`` wiring is proved twice: the adapter
notes the search it attached, and the real route carries a search a bound adapter noted.
"""

from __future__ import annotations

import dataclasses
import sys
import types
from typing import Any

import pytest
from fastapi.testclient import TestClient
from hex_service_kit import provenance
from tests.conftest import LOOPBACK_PEER
from tests.fixtures import sample_docs

from enterprise_kb.adapters.gcp.gemini_grounding import GeminiGoogleSearchGroundingAdapter
from enterprise_kb.adapters.gcp.gemini_llm import GeminiLLMAdapter
from enterprise_kb.adapters.local.llm import LocalDeterministicLLMAdapter
from enterprise_kb.adapters.local.retrieval import LocalFtsRetrievalAdapter
from enterprise_kb.api import app as app_module
from enterprise_kb.api import deps
from enterprise_kb.config import Container, LocalSettings, ModelSettings, Settings
from enterprise_kb.domain.models import (
    KbQuery,
    LlmMessage,
    LlmRequest,
    LlmResponse,
    RetrievedPassage,
)

CONFIG = "config/settings.yaml"
ANSWERED_BY = "x-answered-by"
SEARCH_USED = "x-search-used"


class _SeededRetrieval(LocalFtsRetrievalAdapter):
    """The synthetic corpus, handed to the domain for ACL admission (see test_api_identity)."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._candidates = list(sample_docs.SAMPLE_PASSAGES)
        self.seed(self._candidates)

    def retrieve(self, query: KbQuery) -> list[RetrievedPassage]:
        return list(self._candidates)[: query.top_k]


def _settings() -> Settings:
    return dataclasses.replace(
        Settings.load(CONFIG),
        profile="local",
        local=LocalSettings(db_path=":memory:", audit_path=":memory:", ledger_path=":memory:"),
    )


def _client(monkeypatch: pytest.MonkeyPatch, llm: Any | None = None) -> TestClient:
    settings = _settings()
    container = Container(settings)
    container.__dict__["retrieval"] = _SeededRetrieval(settings)
    if llm is not None:
        container.__dict__["llm"] = llm
    monkeypatch.setattr(deps, "get_container", lambda: container)
    monkeypatch.setattr(app_module.deps, "get_container", lambda: container)
    return TestClient(app_module.app, client=LOOPBACK_PEER)


def _answer(client: TestClient, **headers: str) -> dict[str, str]:
    response = client.post(
        "/v1/answer",
        json={"query": sample_docs.SAMPLE_QUERY, "acl_principals": []},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return dict(response.headers)


# --------------------------------------------------------------------------- #
# The route: what the bound adapter noted, and nothing when nothing was noted
# --------------------------------------------------------------------------- #
def test_an_answer_names_the_model_the_bound_adapter_answered_as(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Under ``local`` that is the stub, the same name ``/healthz`` gave the pill beforehand."""
    headers = _answer(_client(monkeypatch))
    assert headers[ANSWERED_BY] == _settings().generator_model == "deterministic-offline-stub"
    assert SEARCH_USED not in headers, "no search tool was attached to any call"


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/healthz", None),
        ("POST", "/v1/search", {"query": sample_docs.SAMPLE_QUERY, "acl_principals": []}),
    ],
)
def test_a_request_no_model_answered_names_no_model(
    monkeypatch: pytest.MonkeyPatch, method: str, path: str, body: dict[str, Any] | None
) -> None:
    response = _client(monkeypatch).request(method, path, json=body)
    assert response.status_code == 200, response.text
    assert ANSWERED_BY not in response.headers
    assert SEARCH_USED not in response.headers


class _SearchingLLM(LocalDeterministicLLMAdapter):
    """The real local generator, plus what an adapter that searched notes while it calls."""

    def generate(self, request: LlmRequest) -> LlmResponse:
        provenance.note_model("fake-searching-model")
        provenance.note_search()
        return super().generate(request)


def test_the_route_carries_a_search_the_adapter_noted_and_forgets_it_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = _answer(_client(monkeypatch, llm=_SearchingLLM(_settings())))
    assert headers[SEARCH_USED] == "true"
    assert headers[ANSWERED_BY].split(", ")[0] == "fake-searching-model"
    # The next request is a fresh record: a search never leaks into a later answer.
    assert SEARCH_USED not in _answer(_client(monkeypatch))


def test_a_cross_origin_console_is_allowed_to_read_both_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A standalone console reads these from another origin; unlisted, the browser hides them."""
    origin = app_module._DEV_ORIGINS[0]
    headers = _answer(_client(monkeypatch), Origin=origin)
    assert headers["access-control-allow-origin"] == origin
    exposed = {name.strip().lower() for name in headers["access-control-expose-headers"].split(",")}
    assert {ANSWERED_BY, SEARCH_USED} <= exposed


# --------------------------------------------------------------------------- #
# The managed adapters note what they called, against a faked SDK
# --------------------------------------------------------------------------- #
class _Recorder:
    """Stands in for any ``google.genai.types`` constructor: keeps what it was built with."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.args = args
        self.kwargs = kwargs

    @classmethod
    def from_text(cls, *, text: str) -> _Recorder:
        return cls(text=text)


class _FakeModels:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def generate_content(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return types.SimpleNamespace(text="policy", usage_metadata=None, candidates=[])


@pytest.fixture
def fake_sdk(monkeypatch: pytest.MonkeyPatch) -> _FakeModels:
    genai_types = types.SimpleNamespace(
        Content=_Recorder,
        Part=_Recorder,
        GenerateContentConfig=_Recorder,
        ThinkingConfig=_Recorder,
        Tool=_Recorder,
        GoogleSearch=_Recorder,
        ThinkingLevel=types.SimpleNamespace(LOW="LOW", HIGH="HIGH"),
    )
    google = types.ModuleType("google")
    genai = types.ModuleType("google.genai")
    genai.types = genai_types  # type: ignore[attr-defined]
    google.genai = genai  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.genai", genai)
    return _FakeModels()


def _gcp_settings() -> Settings:
    # Distinct ids, so a note or a resolver that read the wrong setting cannot pass by the
    # coincidence that the shipped settings give every tier the same model.
    return dataclasses.replace(
        Settings.load(CONFIG),
        profile="gcp",
        grounding_enabled=True,
        models=ModelSettings(reasoning="reasoning-model", triage="triage-model"),
    )


def _request() -> LlmRequest:
    return LlmRequest(messages=(LlmMessage(role="user", content="q"),))


def test_the_gemini_adapter_notes_the_model_it_called(fake_sdk: _FakeModels) -> None:
    adapter = GeminiLLMAdapter(_gcp_settings())
    adapter._client = types.SimpleNamespace(models=fake_sdk)
    with provenance.scope() as record:
        adapter.generate(_request())
    assert record.models == [fake_sdk.calls[0]["model"]] == ["reasoning-model"]
    assert record.search_used is False, "no search tool is attached to a generate call"

    with provenance.scope() as record:
        adapter.generate(dataclasses.replace(_request(), model="an-explicit-model"))
        adapter.classify("text", ["faq", "policy"])
    assert record.models == ["an-explicit-model", "triage-model"]
    assert [call["model"] for call in fake_sdk.calls[1:]] == record.models


def test_a_failed_gemini_call_notes_nothing(fake_sdk: _FakeModels) -> None:
    def refuse(**kwargs: Any) -> Any:
        raise RuntimeError("quota")

    adapter = GeminiLLMAdapter(_gcp_settings())
    adapter._client = types.SimpleNamespace(models=types.SimpleNamespace(generate_content=refuse))
    with provenance.scope() as record, pytest.raises(RuntimeError):
        adapter.generate(_request())
    assert record.models == []


def test_generator_model_is_the_model_the_gcp_adapter_calls(fake_sdk: _FakeModels) -> None:
    """What the pill shows before an answer is what the adapter then answers as."""
    settings = _gcp_settings()
    adapter = GeminiLLMAdapter(settings)
    adapter._client = types.SimpleNamespace(models=fake_sdk)
    adapter.generate(_request())
    assert fake_sdk.calls[0]["model"] == settings.generator_model


def test_the_grounding_adapter_notes_its_model_and_the_search_it_attached(
    fake_sdk: _FakeModels,
) -> None:
    adapter = GeminiGoogleSearchGroundingAdapter(_gcp_settings())
    adapter._client = types.SimpleNamespace(models=fake_sdk)
    with provenance.scope() as record:
        adapter.ground("what changed in the outsourcing guidelines")
    tools = fake_sdk.calls[0]["config"].kwargs["tools"]
    assert "google_search" in tools[0].kwargs, "the search tool was attached to this call"
    assert record.search_used is True
    assert record.models == [fake_sdk.calls[0]["model"]]


def test_switched_off_grounding_notes_no_search(fake_sdk: _FakeModels) -> None:
    settings = dataclasses.replace(_gcp_settings(), grounding_enabled=False)
    with provenance.scope() as record:
        assert GeminiGoogleSearchGroundingAdapter(settings).ground("q") == []
    assert record.search_used is False and record.models == []


# --------------------------------------------------------------------------- #
# No flag can move the pill away from the model that answers
# --------------------------------------------------------------------------- #
def test_the_hard_reasoning_flag_is_gone() -> None:
    """The latent false banner: a flag that moved the pill but not the model that answered.

    ``models.use_hard_reasoning`` named ``models.hard_reasoning`` as the model while the Gemini
    adapter called ``request.model or models.reasoning`` and never read the flag. Both settings
    are deleted, so nothing is left for a resolver to name that the adapter does not call.
    """
    fields = {f.name for f in dataclasses.fields(ModelSettings)}
    assert "use_hard_reasoning" not in fields and "hard_reasoning" not in fields
    with open(CONFIG, encoding="utf-8") as handle:
        assert "hard_reasoning" not in handle.read()
