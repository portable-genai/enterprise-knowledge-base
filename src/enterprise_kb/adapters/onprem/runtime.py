"""On-prem placeholder for ``AgentRuntimePort`` : the Google Distributed Cloud target.

One of the reversibility (P-02, P-12) migration placeholders: in the managed profile
this port binds to the Agent Runtime (ex-Agent Engine) adapter; switching ``profile`` to
``onprem`` rebinds it here. The adapter constructs cleanly with **no external
dependencies** and structurally satisfies the same Protocol as the managed adapter, so
the contract tests prove interface parity. Porting A2 to an on-premise hosting platform
is *only* a matter of filling these bodies in : the domain orchestration is unchanged.
"""

from __future__ import annotations

from ...config import Settings
from ...domain.models import GroundedAnswer, Session

_MESSAGE = (
    "On-prem AgentRuntimePort adapter is a migration placeholder; implement against your "
    "on-premise platform. Core domain logic is unchanged."
)


class OnPremAgentRuntimeAdapter:
    """Placeholder agent-runtime adapter for the on-prem (Google Distributed Cloud) profile."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def query(self, session: Session, message: str) -> GroundedAnswer:
        raise NotImplementedError(_MESSAGE)

    def health(self) -> bool:
        raise NotImplementedError(_MESSAGE)
