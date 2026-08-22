"""Remote-platform audit adapter — thin HTTP client to A5.

In the full platform deployment, immutable audit records are written through the shared
``agent-observability`` service (WORM Cloud Logging locked bucket + Cloud Trace +
FinOps) instead of A2 calling Cloud Logging directly. This adapter implements
:class:`AuditSinkPort` by POSTing the already-redacted :class:`AuditEvent` to the
observability service's ``/v1/audit`` endpoint, which returns ``202 Accepted`` (SPEC §6,
A5 contract).

PII is removed at the domain boundary (P-04) before the event reaches this adapter, so the
JSON body it sends is already safe to persist. The base URL is read from
``HRZ_OBSERVABILITY_URL`` with a localhost default.
"""

from __future__ import annotations

import httpx

from ...domain.errors import KnowledgeBaseError
from ...domain.models import AuditEvent
from ...domain.serialization import to_jsonable
from ...envread import setting_or_default
from . import _s2s

_DEFAULT_URL = "http://localhost:8085"
_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


class RemoteAuditError(KnowledgeBaseError):
    """Raised when the remote observability service returns a non-2xx response."""


class RemoteAuditAdapter:
    """HTTP client for the A5 ``agent-observability`` audit sink."""

    def __init__(self, settings: object) -> None:
        self._settings = settings
        self._base_url = _s2s.validate_base_url(
            setting_or_default("HRZ_OBSERVABILITY_URL", _DEFAULT_URL),
            service="observability sink",
        )

    def record(self, event: AuditEvent) -> None:
        """Write an immutable, already-redacted audit record (WORM) via A5."""
        payload = to_jsonable(event)
        url = f"{self._base_url}/v1/audit"
        try:
            response = httpx.post(url, json=payload, timeout=_TIMEOUT, headers=_s2s.headers())
        except httpx.HTTPError as exc:  # network / connection / timeout
            raise RemoteAuditError(f"observability audit request to {url} failed: {exc}") from exc
        if response.status_code // 100 != 2:
            raise RemoteAuditError(
                f"observability audit {url} returned {response.status_code}: {response.text[:500]}"
            )
