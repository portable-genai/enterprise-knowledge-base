"""Remote-platform redaction adapter : thin HTTP client to A1.

When A2 runs inside the full Gemini Enterprise Agent Platform deployment, PII redaction
is delegated to the shared ``agent-guardrail-gateway`` service (backed by Sensitive
Data Protection / DLP) rather than calling DLP directly. This adapter implements
:class:`PIIRedactionPort` by POSTing to that gateway's ``/v1/redact`` endpoint and parsing
the response back into the domain :class:`RedactionResult` (SPEC §6, A1 contract).

A2 ingests bank documents that may carry PII, so redaction runs at both the ingest and
the serve boundary (R1, P-04). The adapter follows the construction convention
``__init__(self, settings)`` and reads its base URL from ``HRZ_GUARDRAIL_URL`` (sensible
localhost default), so nothing GCP-specific is required to construct or exercise it.
"""

from __future__ import annotations

import httpx

from ...domain.errors import KnowledgeBaseError
from ...domain.models import RedactionFinding, RedactionResult
from ...envread import setting_or_default
from . import _s2s

_DEFAULT_URL = "http://localhost:8080"
_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


class RemoteRedactionError(KnowledgeBaseError):
    """Raised when the remote guardrail gateway returns a non-2xx response."""


class RemoteRedactionAdapter:
    """HTTP client for the A1 ``agent-guardrail-gateway`` redaction endpoint."""

    def __init__(self, settings: object) -> None:
        self._settings = settings
        self._base_url = _s2s.validate_base_url(
            setting_or_default("HRZ_GUARDRAIL_URL", _DEFAULT_URL), service="redaction gateway"
        )

    def redact(self, text: str) -> RedactionResult:
        """De-identify ``text`` via the A1 gateway's ``/v1/redact`` endpoint."""
        url = f"{self._base_url}/v1/redact"
        try:
            response = httpx.post(
                url, json={"text": text}, timeout=_TIMEOUT, headers=_s2s.headers()
            )
        except httpx.HTTPError as exc:  # network / connection / timeout
            raise RemoteRedactionError(f"redaction request to {url} failed: {exc}") from exc
        if response.status_code // 100 != 2:
            raise RemoteRedactionError(
                f"redaction {url} returned {response.status_code}: {response.text[:500]}"
            )
        return self._parse(response.json(), fallback=text)

    @staticmethod
    def _parse(body: dict, fallback: str) -> RedactionResult:
        findings = tuple(
            RedactionFinding(
                info_type=str(item.get("info_type", "")),
                count=int(item.get("count", 1) or 1),
            )
            for item in (body.get("findings") or ())
            if item.get("info_type")
        )
        return RedactionResult(text=str(body.get("text", fallback)), findings=findings)
