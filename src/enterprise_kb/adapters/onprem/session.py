"""On-prem placeholder for ``SessionPort`` : the Google Distributed Cloud target.

One of the reversibility (P-02, P-12) migration placeholders: in the managed profile
this port binds to the Agent Platform Sessions adapter; switching ``profile`` to
``onprem`` rebinds it here. The adapter constructs cleanly with **no external
dependencies** and structurally satisfies the same Protocol as the managed adapter, so
the contract tests prove interface parity. Porting per-case conversation state to an
on-premise store is *only* a matter of filling these bodies in : the domain is unchanged.
"""

from __future__ import annotations

from ...config import Settings
from ...domain.models import LlmMessage, Session

_MESSAGE = (
    "On-prem SessionPort adapter is a migration placeholder; implement against your "
    "on-premise platform. Core domain logic is unchanged."
)


class OnPremSessionAdapter:
    """Placeholder session adapter for the on-prem (Google Distributed Cloud) profile."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def create_session(self, user_id: str, case_id: str | None = None) -> Session:
        raise NotImplementedError(_MESSAGE)

    def get(self, session_id: str, user_id: str) -> Session | None:
        raise NotImplementedError(_MESSAGE)

    def append(self, session_id: str, user_id: str, message: LlmMessage) -> None:
        raise NotImplementedError(_MESSAGE)

    def history(self, session_id: str, user_id: str) -> list[LlmMessage]:
        raise NotImplementedError(_MESSAGE)
