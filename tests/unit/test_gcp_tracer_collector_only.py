"""The gcp tracer exports only through the agent-observability collector.

The hand-written Cloud Trace tracer this adapter replaced exported straight to Cloud Trace, around
the collector's GenAI-content redaction. These pin the replacement's two promises: it is the kit's
tracer under this service's name, and it refuses to start with no collector to export to
(decision D1 of the guardrail/registry/observability plan). Both run with no OpenTelemetry SDK
installed, so the offline gate holds them.
"""

from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext
from typing import Any

import pytest
from hex_service_kit import tracing
from hex_service_kit.netdefaults import ConfiguredEmptyError
from hex_service_kit.observability import TokenUsage

from enterprise_kb.adapters.gcp.tracer import CloudTracerAdapter


def _adapter() -> CloudTracerAdapter:
    # The adapter keeps its settings only for the one-Settings-argument adapter contract.
    settings: Any = None
    return CloudTracerAdapter(settings)


def test_refuses_to_trace_with_no_collector_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(tracing.ENDPOINT_ENV, raising=False)
    with pytest.raises(tracing.CollectorEndpointRequiredError):
        _adapter().span("probe")


def test_refuses_an_emptied_collector_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(tracing.ENDPOINT_ENV, "")
    with pytest.raises(ConfiguredEmptyError):
        _adapter().span("probe")


def test_is_the_kit_tracer_under_this_service_name(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, Any]] = []

    class _KitTracer:
        def span(self, name: str, **attributes: str) -> AbstractContextManager[None]:
            calls.append(("span", (name, attributes)))
            return nullcontext()

        def record_token_usage(self, usage: TokenUsage, model: str) -> None:
            calls.append(("usage", (usage, model)))

    def _build_tracer(*, service: str) -> _KitTracer:
        calls.append(("build", service))
        return _KitTracer()

    monkeypatch.setattr(tracing, "build_tracer", _build_tracer)
    adapter = _adapter()
    usage = TokenUsage(input_tokens=3, output_tokens=2)
    with adapter.span("unit.of.work", action="probe"):
        pass
    adapter.record_token_usage(usage, "a-model")

    assert calls == [
        ("build", "enterprise-knowledge-base"),
        ("span", ("unit.of.work", {"action": "probe"})),
        ("usage", (usage, "a-model")),
    ]
