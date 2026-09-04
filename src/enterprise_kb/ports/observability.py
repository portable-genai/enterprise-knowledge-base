"""Observability ports : the A5 (audit/trace) and A4 (eval gate) concerns.

Primary GCP adapters: **Cloud Logging locked WORM bucket** for immutable audit,
**Cloud Trace via OpenTelemetry** for reasoning-loop traces (message content capture
OFF so PII never reaches a span), and the **Gen AI evaluation service** for the
in-repo promotion gate (retrieval recall, ACL correctness, citation accuracy, safety).

``ObservabilityTracerPort`` is NOT written out here. It and its ``TokenUsage`` value type come
from ``hex-service-kit``, for the same reason a shared value type always should: sixteen
repositories each hand-copied this Protocol, and by the time anyone compared them they had
drifted. A Protocol copied into N repositories is N Protocols, and only one of them gets fixed
when a defect is found. The import is typing-only, so the offline profile pays nothing for it:
no OpenTelemetry, no SDK. The OpenTelemetry implementation lives behind the commons ``otel``
extra and is reached only by the ``gcp`` adapter.

``AuditSinkPort`` stays declared here on purpose. It is typed in THIS repo's vocabulary : it
takes A2's :class:`~enterprise_kb.domain.models.AuditEvent`, with the redacted prompt/response
and page-level citations a governed RAG store audits, so it is not the fleet's type to share.

``EvaluationGatePort`` also stays here as an intentional runtime boundary. Its method returns
enterprise-knowledge-base's domain ``EvalReport``, while ``agent-eval-kit`` owns only the
development-time command scaffold and mutation helpers. Importing that dev package from the port
would make every serving process depend on test tooling. The local Protocol keeps structural
compatibility without that dependency; a subprocess contract test blocks ``agent_eval_kit`` and
imports the serving app.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from hex_service_kit.observability import ObservabilityTracerPort, TokenUsage

from ..domain.models import AuditEvent, EvalReport

__all__ = [
    "AuditSinkPort",
    "EvaluationGatePort",
    "ObservabilityTracerPort",
    "TokenUsage",
]


@runtime_checkable
class AuditSinkPort(Protocol):
    def record(self, event: AuditEvent) -> None:
        """Write an immutable, already-redacted audit record (WORM)."""
        ...


@runtime_checkable
class EvaluationGatePort(Protocol):
    def evaluate(self, dataset_path: str) -> EvalReport:
        """Run the eval suite over a golden dataset and return a pass/fail report."""
        ...
