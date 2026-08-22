"""On-prem placeholder for ``EvaluationGatePort`` : the Google Distributed Cloud target.

One of the reversibility (P-02, P-12) migration placeholders: in the managed profile
this port binds to the Gen AI evaluation-service adapter; switching ``profile`` to
``onprem`` rebinds it here. The adapter constructs cleanly with **no external
dependencies** and structurally satisfies the same Protocol as the managed adapter, so
the contract tests prove interface parity. ``evaluate`` deliberately raises rather than
returning a vacuously-passing report: an unimplemented promotion gate must never wave a
build through unevaluated, so porting on-premise *must* supply a real eval backend.
Filling this body in is the only change required.
"""

from __future__ import annotations

from ...config import Settings
from ...domain.models import EvalReport

_MESSAGE = (
    "On-prem EvaluationGatePort adapter is a migration placeholder; implement against "
    "your on-premise platform. Core domain logic is unchanged."
)


class OnPremEvalAdapter:
    """Placeholder evaluation-gate adapter for the on-prem (Google Distributed Cloud) profile."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def evaluate(self, dataset_path: str) -> EvalReport:
        raise NotImplementedError(_MESSAGE)
