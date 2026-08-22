"""Fail-closed managed Agent Runtime seam.

The current managed reference architecture serves governed reads through the IAP-protected
Cloud Run API/UI. It deliberately deploys and advertises no Agent Runtime because there is no
implemented transport that turns immutable invocation metadata into the verified tenant and
entitlement context required by the domain. Keeping an unreachable best-effort parser here would
make that unsafe boundary easy to re-enable accidentally, so this adapter exposes only an
explicit refusal while preserving ``AgentRuntimePort`` profile parity.
"""

from __future__ import annotations

from ...config import Settings
from ...domain.models import GroundedAnswer, Session


class AgentRuntimeAdapter:
    """Refuse managed agent invocation until the verified-context contract is built."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def query(self, session: Session, message: str) -> GroundedAnswer:
        """Never treat client-owned session/message data as authenticated identity."""
        del session, message
        raise RuntimeError(
            "managed Agent Runtime query is disabled until a verified tenant/entitlement "
            "transport provider and typed cited response contract are configured"
        )

    def health(self) -> bool:
        """Return false because this optional managed surface is intentionally unavailable."""
        return False
