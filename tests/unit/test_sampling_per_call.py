"""Sampling is decided per call: pinned where the output is compared, free where it is prose.

**History.** This file began as "the grounded request must not sample by default". On
2026-08-26 two runs of one identical case against a sibling service's deployment
(`cdd-sow-research`), minutes apart, returned `score` 0.5 then 0.0 and `confidence` 0.4 then
1.0: its shared request builder defaulted to `temperature=0.2`, so every grounded call sampled.
The fix here was a default of 0.0 on the type and on the builder.

**What changed (owner decision, 2026-09-23).** A default that pins every call also pins the ones
whose output is only ever read as prose, and some models (Opus 5, Fable 5) refuse the parameter
outright. So the type's default is now ``None``, which sends NO temperature, and each call site
says what it needs: ``0.0`` where the output is extracted, classified, scored or compared, and
nothing where it drafts, narrates or explains. The finding above is kept where it applies: both
grounded calls here return a score that drives the review escalation, so both still pin, and
that is asserted on the requests the real service builds, not on a default.

**Temperature 0 is not a promise of determinism, and nothing here asserts one.** A hosted model
can still vary across batching and model revisions. It is the strongest thing a caller controls.
"""

from __future__ import annotations

import ast
import inspect
import sys
import types
from pathlib import Path
from typing import Any

import pytest
from hex_service_kit.localmodel import LocalModelClient, LocalModelSettings
from tests.fixtures import sample_docs

from enterprise_kb.adapters.gcp.gemini_llm import GeminiLLMAdapter
from enterprise_kb.adapters.live.llm import LocalModelLLMAdapter
from enterprise_kb.config import Settings
from enterprise_kb.domain import _grounded
from enterprise_kb.domain.kb_service import _ANSWER_SCHEMA, _CRITIQUE_SCHEMA
from enterprise_kb.domain.models import LlmMessage, LlmRequest

ROOT_AGENT = Path("src/enterprise_kb/agent/root_agent.py")


def test_a_request_that_says_nothing_sends_no_temperature() -> None:
    """Free is the absence of the parameter, never 1.0 or any other number somebody chose."""
    assert LlmRequest.__dataclass_fields__["temperature"].default is None
    assert inspect.signature(_grounded.build_llm_request).parameters["temperature"].default is None


def test_both_grounded_calls_pin_because_their_score_drives_review(kb_service, llm) -> None:
    """The answer (citations + confidence) and the self-critique (label + confidence) are compared.

    Driven through the real service with the recording local generator, so what is checked is
    the request each call site actually builds.
    """
    kb_service.answer(
        sample_docs.SAMPLE_QUERY,
        actor="user:jane@bank.test",
        acl_principals=["user:jane@bank.test"],
    )
    by_schema = {id(request.response_schema): request for request in llm.requests}
    assert set(by_schema) == {id(_ANSWER_SCHEMA), id(_CRITIQUE_SCHEMA)}, "both calls ran"
    assert by_schema[id(_ANSWER_SCHEMA)].temperature == 0.0
    assert by_schema[id(_CRITIQUE_SCHEMA)].temperature == 0.0


def test_the_root_agent_drafts_with_no_temperature() -> None:
    """The drafting call site: the ADK root agent narrates from its tools' governed results.

    Read from the source because building the agent needs ``google-adk``, which the offline gate
    never installs. Its generation config must carry no ``temperature`` keyword at all.
    """
    configs = [
        node
        for node in ast.walk(ast.parse(ROOT_AGENT.read_text(encoding="utf-8")))
        if isinstance(node, ast.Call)
        and getattr(node.func, "attr", getattr(node.func, "id", "")) == "GenerateContentConfig"
    ]
    assert configs, "the root agent no longer builds a generation config; re-point this test"
    for config in configs:
        assert "temperature" not in {keyword.arg for keyword in config.keywords}


# --------------------------------------------------------------------------- #
# The adapters: None is omitted on the wire, a pin is sent as given
# --------------------------------------------------------------------------- #
class _Config:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


@pytest.fixture
def genai_types(monkeypatch: pytest.MonkeyPatch) -> Any:
    namespace = types.SimpleNamespace(
        GenerateContentConfig=_Config,
        ThinkingConfig=_Config,
        ThinkingLevel=types.SimpleNamespace(LOW="LOW", HIGH="HIGH"),
    )
    google = types.ModuleType("google")
    genai = types.ModuleType("google.genai")
    genai.types = namespace  # type: ignore[attr-defined]
    google.genai = genai  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.genai", genai)
    return namespace


def _request(temperature: float | None) -> LlmRequest:
    return LlmRequest(messages=(LlmMessage(role="user", content="q"),), temperature=temperature)


def test_the_gemini_adapter_omits_a_free_temperature_and_sends_a_pinned_one(
    genai_types: Any,
) -> None:
    adapter = GeminiLLMAdapter(Settings(profile="gcp"))
    assert "temperature" not in adapter._build_config(_request(None), genai_types).kwargs
    assert adapter._build_config(_request(0.0), genai_types).kwargs["temperature"] == 0.0


def test_the_live_adapter_passes_a_free_temperature_through_as_none() -> None:
    sent: list[bytes] = []

    def transport(url: str, body: bytes | None, timeout: float) -> bytes:
        assert body is not None
        sent.append(body)
        return b'{"model": "m", "choices": [{"message": {"content": "prose"}}]}'

    client = LocalModelClient(LocalModelSettings(), transport=transport)
    adapter = LocalModelLLMAdapter(Settings(profile="live"), client=client)
    adapter.generate(_request(None))
    adapter.generate(_request(0.0))
    assert b'"temperature"' not in sent[0], "a free call must not send a temperature"
    assert b'"temperature": 0.0' in sent[1]
