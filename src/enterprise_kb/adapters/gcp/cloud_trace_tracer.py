"""Cloud Trace tracer adapter — reasoning-loop observability for system A2.

Backs the domain ``ObservabilityTracerPort`` with **Cloud Trace** via OpenTelemetry.
``span(...)`` opens an OTel span around a unit of agent work; ``record_token_usage(...)``
emits token counts as OTel metrics for FinOps dashboards (A5).

Privacy contract (P-04 / SPEC §3 gotcha): **message content capture is OFF**. Only ids
and metadata (action, model, counts) ever land on a span — never the prompt, the
retrieved passages, or the model response. Callers must pass only non-PII attributes.

OpenTelemetry and the Cloud Trace exporter are imported lazily so the on-prem and test
profiles import this module without them installed.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from typing import Any

from hex_service_kit.observability import TokenUsage

from ...config import Settings

_INSTRUMENTATION_SCOPE = "enterprise_kb.tracing"


class CloudTraceTracerAdapter:
    """OpenTelemetry tracer exporting spans to Cloud Trace (content capture OFF)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._configured = False
        self._tracer: Any | None = None
        self._token_counters: dict[str, Any] = {}

    # ------------------------------------------------------------------ #
    # Lazy OTel/exporter configuration
    # ------------------------------------------------------------------ #
    def _ensure_configured(self) -> Any:
        """Configure the TracerProvider + Cloud Trace exporter once; return a tracer."""
        if self._configured and self._tracer is not None:
            return self._tracer
        try:
            from opentelemetry import trace  # lazy
            from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter  # lazy
            from opentelemetry.sdk.resources import Resource  # lazy
            from opentelemetry.sdk.trace import TracerProvider  # lazy
            from opentelemetry.sdk.trace.export import BatchSpanProcessor  # lazy
        except Exception:  # noqa: BLE001 — tracing must degrade gracefully
            self._configured = True
            self._tracer = None
            return None

        # verify: https://cloud.google.com/trace/docs/setup/python-ot
        service_name = self._settings.agent_engine.display_name or "enterprise-knowledge-base"
        resource = Resource.create(
            {
                "service.name": service_name,
                "cloud.region": self._settings.region,
                "cloud.account.id": self._settings.project_id,
            }
        )
        provider = trace.get_tracer_provider()
        if not isinstance(provider, TracerProvider):
            provider = TracerProvider(resource=resource)
            exporter = CloudTraceSpanExporter(project_id=self._settings.project_id)
            provider.add_span_processor(BatchSpanProcessor(exporter))
            trace.set_tracer_provider(provider)
        self._tracer = trace.get_tracer(_INSTRUMENTATION_SCOPE)
        self._configured = True
        return self._tracer

    # ------------------------------------------------------------------ #
    # ObservabilityTracerPort
    # ------------------------------------------------------------------ #
    def span(self, name: str, **attributes: str) -> AbstractContextManager[None]:
        """Open a trace span named ``name`` carrying only id/metadata attributes.

        Never pass prompt/response/passage content here — only non-PII metadata such as
        ``action``, ``model``, ``actor`` id, or ``trace_id``.
        """
        tracer = self._ensure_configured()
        if tracer is None:
            return nullcontext()
        return self._span(tracer, name, attributes)

    @contextmanager
    def _span(self, tracer: Any, name: str, attributes: dict[str, str]) -> Iterator[None]:
        with tracer.start_as_current_span(name) as otel_span:
            for key, value in attributes.items():
                # Defensive: coerce to str; metadata only, never content.
                otel_span.set_attribute(key, str(value))
            yield

    def record_token_usage(self, usage: TokenUsage, model: str) -> None:
        """Emit token-usage as OTel metrics; fall back to a structured log if needed."""
        if self._emit_metric(usage, model):
            return
        self._log_usage(usage, model)

    # ------------------------------------------------------------------ #
    # Metrics
    # ------------------------------------------------------------------ #
    def _emit_metric(self, usage: TokenUsage, model: str) -> bool:
        try:
            from opentelemetry import metrics  # lazy
        except Exception:  # noqa: BLE001
            return False
        attrs = {"model": model, "service": "enterprise-knowledge-base"}
        try:
            meter = metrics.get_meter(_INSTRUMENTATION_SCOPE)
            for name, value in (
                ("gen_ai.usage.input_tokens", usage.input_tokens),
                ("gen_ai.usage.output_tokens", usage.output_tokens),
                ("gen_ai.usage.thinking_tokens", usage.thinking_tokens),
            ):
                counter = self._token_counters.get(name)
                if counter is None:
                    counter = meter.create_counter(name, unit="{token}")
                    self._token_counters[name] = counter
                counter.add(value, attributes=attrs)
        except Exception:  # noqa: BLE001 — metrics must never break the request path
            return False
        return True

    def _log_usage(self, usage: TokenUsage, model: str) -> None:
        try:
            from google.cloud import logging_v2  # lazy
        except Exception:  # noqa: BLE001
            return
        try:
            client = logging_v2.Client(project=self._settings.project_id)
            client.logger("enterprise-knowledge-base-finops").log_struct(
                {
                    "model": model,
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "thinking_tokens": usage.thinking_tokens,
                },
                severity="INFO",
            )
        except Exception:  # noqa: BLE001
            return
