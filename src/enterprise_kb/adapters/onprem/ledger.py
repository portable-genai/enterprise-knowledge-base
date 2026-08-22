"""On-prem placeholder for ``FreshnessLedgerPort`` : the Google Distributed Cloud target.

One of the reversibility (P-02, P-12) migration placeholders: in the managed profile
this port binds to the AlloyDB freshness/residency-ledger adapter; switching ``profile``
to ``onprem`` rebinds it here. The adapter constructs cleanly with **no external
dependencies** and structurally satisfies the same Protocol as the managed adapter, so
the contract tests prove interface parity. Porting the freshness + residency ledger to
an on-premise relational store is *only* a matter of filling these bodies in : the
domain freshness policy is unchanged.
"""

from __future__ import annotations

from datetime import datetime

from ...config import Settings
from ...domain.models import FreshnessRecord

_MESSAGE = (
    "On-prem FreshnessLedgerPort adapter is a migration placeholder; implement against "
    "your on-premise platform. Core domain logic is unchanged."
)


class OnPremLedgerAdapter:
    """Placeholder freshness-ledger adapter for the on-prem (Google Distributed Cloud) profile."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def get(self, document_id: str, tenant: str = "") -> FreshnessRecord | None:
        raise NotImplementedError(_MESSAGE)

    def upsert(self, record: FreshnessRecord) -> None:
        raise NotImplementedError(_MESSAGE)

    def delete(self, document_id: str, tenant: str = "") -> None:
        raise NotImplementedError(_MESSAGE)

    def list_expired(self, now: datetime | None = None) -> list[FreshnessRecord]:
        raise NotImplementedError(_MESSAGE)

    def all(self) -> list[FreshnessRecord]:
        raise NotImplementedError(_MESSAGE)
