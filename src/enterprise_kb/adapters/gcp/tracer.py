"""Managed ObservabilityTracerPort: OpenTelemetry through the agent-observability collector.

This adapter is deliberately thin. The OpenTelemetry work lives in ``hex_service_kit.tracing`` (the
kit's ``otel`` extra): the OTLP exporter, the ID token the private Cloud Run collector requires,
and the rule that a tracing fault never becomes a request fault are implemented once for the fleet
rather than per repository.

Spans go OTLP to the collector named by ``OTEL_EXPORTER_OTLP_ENDPOINT``, which deletes GenAI
content attributes before anything reaches a Google sink. There is no direct Cloud Trace path.
This repository used to carry its own ``CloudTraceSpanExporter`` tracer, which exported straight
to Cloud Trace and so around that redaction; it was retired onto the kit in phase P4 of the
guardrail/registry/observability plan. The kit refuses to build a tracer when the endpoint is
unset or empty (decision D1), so a ``gcp`` deployment that has not wired the collector fails
loudly on its first span instead of exporting around it.

Only structural attributes go on a span, never message content: callers pass ids and metadata,
and the collector's redaction is the backstop, not the contract.

The kit import is lazy (practice A5): the local and on-prem profiles import this package with no
cloud SDK installed.
"""

from __future__ import annotations

from contextlib import AbstractContextManager

from hex_service_kit.observability import ObservabilityTracerPort, TokenUsage

from ...config import Settings

#: The ``service.name`` every span carries in the trace backend and the topology view.
_SERVICE_NAME = "enterprise-knowledge-base"


class CloudTracerAdapter:
    """Binds the tracer port to the kit's collector-only OpenTelemetry tracer."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._delegate: ObservabilityTracerPort | None = None

    def _tracer(self) -> ObservabilityTracerPort:
        if self._delegate is None:
            from hex_service_kit.tracing import build_tracer  # noqa: PLC0415

            self._delegate = build_tracer(service=_SERVICE_NAME)
        return self._delegate

    def span(self, name: str, **attributes: str) -> AbstractContextManager[None]:
        """Open a span for one unit of work, carrying structural attributes only."""
        return self._tracer().span(name, **attributes)

    def record_token_usage(self, usage: TokenUsage, model: str) -> None:
        """Record what one model call consumed on the current span (counts only)."""
        self._tracer().record_token_usage(usage, model)
