"""On-prem placeholder for ``MemoryPort`` : the Google Distributed Cloud target.

One of the reversibility (P-02, P-12) migration placeholders: in the managed profile
this port binds to the Agent Platform Memory Bank adapter; switching ``profile`` to
``onprem`` rebinds it here. The adapter constructs cleanly with **no external
dependencies** and structurally satisfies the same Protocol as the managed adapter, so
the contract tests prove interface parity. Porting durable caller memory to an
on-premise store is *only* a matter of filling these bodies in : the domain is unchanged.
"""

from __future__ import annotations

from ...config import Settings
from ...domain.models import MemoryItem

_MESSAGE = (
    "On-prem MemoryPort adapter is a migration placeholder; implement against your "
    "on-premise platform. Core domain logic is unchanged."
)


class OnPremMemoryAdapter:
    """Placeholder memory adapter for the on-prem (Google Distributed Cloud) profile."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def store(self, item: MemoryItem) -> None:
        raise NotImplementedError(_MESSAGE)

    def search(self, query: str, scope: str = "user", top_k: int = 5) -> list[MemoryItem]:
        raise NotImplementedError(_MESSAGE)
