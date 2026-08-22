"""Local grounding adapter (GroundingPort) : disabled by default (no web egress).

The ``local`` profile's stand-in for the **Gemini ``google_search`` grounding tool**: a
laptop run has no public-web grounding backend, so ``enabled`` is ``False`` and
``ground`` returns no web citations rather than raising, exactly like a sealed
perimeter. The answer pipeline simply skips the grounding step. SDK-free and
unconditional (there is no emulator for web grounding).
"""

from __future__ import annotations

from ...config import Settings
from ...domain.models import WebCitation


class LocalDisabledGroundingAdapter:
    """Disabled web grounding: no public-web egress, returns no citations (never raises)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def enabled(self) -> bool:
        # No public-web grounding backend on a laptop: off by default.
        return False

    def ground(self, query: str, max_results: int = 5) -> list[WebCitation]:
        # No egress: "no web evidence available" is the correct, safe result here.
        return []
