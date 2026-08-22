"""Safety ports : the A1 Guardrail Gateway concerns, expressed as interfaces.

Primary GCP adapters: **Model Armor** (prompt-injection / jailbreak / RAI / malicious
URL screening) and **Sensitive Data Protection / DLP** (``deidentifyContent``) for
GA-grade PII redaction before any model call, index write, or audit write (P-04,
minimise data). A2 ingests bank documents that may carry PII, so redaction runs at both
the ingest and the serve boundary.

A2 ships two interchangeable adapters behind each port: a *direct-GCP* adapter (so the
KB runs standalone) and a *remote-platform* client that delegates to the
``agent-guardrail-gateway`` service (R1) when deployed inside the full platform.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..domain.models import Direction, GuardrailVerdict, RedactionResult


@runtime_checkable
class GuardrailPort(Protocol):
    def screen(self, text: str, direction: Direction) -> GuardrailVerdict:
        """Screen inbound query or outbound answer; may sanitise in place."""
        ...


@runtime_checkable
class PIIRedactionPort(Protocol):
    def redact(self, text: str) -> RedactionResult:
        """De-identify PII so the result is safe to send to a model, index, or audit."""
        ...
