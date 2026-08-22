"""On-prem placeholder for ``GroundingPort`` : the Google Distributed Cloud target.

One of the reversibility (P-02, P-12) migration placeholders. Unlike the other on-prem
stubs, this one returns *benign defaults* rather than raising, because the natural
on-premise posture is a **closed, air-gapped perimeter** with no public-web egress:

* ``enabled`` returns ``False`` : public-web grounding is switched off in a sealed
  on-prem deployment, so the domain pipeline simply skips the grounding step.
* ``ground`` returns ``[]`` : even if a caller invokes it directly, an air-gapped
  default must not crash the answer pipeline; "no web evidence available" is the
  correct, safe result here rather than an error.

This models the conservative default for a sovereign / on-prem install; an actual
on-premise grounding backend (e.g. an internal enterprise search) can be filled in later
without touching domain logic.
"""

from __future__ import annotations

from ...config import Settings
from ...domain.models import WebCitation


class OnPremGroundingAdapter:
    """Placeholder grounding adapter for the on-prem (Google Distributed Cloud) profile.

    Defaults model an air-gapped perimeter: grounding is disabled and returns no web
    citations rather than raising, so the answer pipeline degrades gracefully.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def enabled(self) -> bool:
        # Closed perimeter: public-web grounding is off by default on-prem.
        return False

    def ground(self, query: str, max_results: int = 5) -> list[WebCitation]:
        # Air-gapped default: no public-web egress, so no citations (do not raise).
        return []
