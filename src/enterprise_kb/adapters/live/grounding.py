"""Live grounding adapter (GroundingPort): the OPTIONAL web-search leg of the laptop lane.

Under ``live`` the core model is local and this is the one leg that may call Gemini: the
``google_search`` grounding tool, through the same
:class:`~enterprise_kb.adapters.gcp.gemini_grounding.GeminiGoogleSearchGroundingAdapter`
the managed profiles bind. It is switched by ``KB_GROUNDING_ENABLED``
(``grounding_enabled`` in settings), which is OFF unless an operator turns it on.

Three states, and only one of them leaves the machine:

* ``off``: the switch is off. No SDK is imported and no credential is looked for.
* ``unavailable``: the switch is on but Gemini cannot be reached from here (no
  ``GOOGLE_CLOUD_PROJECT``, the ``[gcp]`` extra not installed, or no Application Default
  Credentials). The app still starts and the core still answers; this leg reports itself
  unavailable, logs why once, and returns no web citations.
* ``on``: the switch is on and credentials resolved; ``ground`` delegates to Gemini. A call
  that fails at request time is logged and yields no citations, because web evidence is
  secondary and must never take the grounded answer down with it.
"""

from __future__ import annotations

import logging
from typing import Any

from ...config import GROUNDING_ENV, Settings
from ...domain.models import WebCitation

_log = logging.getLogger(__name__)

#: ``settings.project_id`` when ``GOOGLE_CLOUD_PROJECT`` is unset (config/settings.yaml).
_PLACEHOLDER_PROJECT = "your-gcp-project"


def _gemini_or_reason(settings: Settings) -> tuple[Any | None, str]:
    """Build the Gemini grounding delegate, or say why it cannot be built on this machine.

    Checks only what can be known locally: the project, the SDK, and that Application Default
    Credentials resolve. Nothing here calls Gemini.
    """
    if not settings.project_id or settings.project_id == _PLACEHOLDER_PROJECT:
        return None, "GOOGLE_CLOUD_PROJECT is not set"
    try:
        import google.auth
        from google import genai  # noqa: F401 - presence check for the [gcp] extra
    except ImportError:
        return None, "the Gemini SDK is not installed (install the [gcp] extra)"
    try:
        google.auth.default()
    except Exception as exc:  # noqa: BLE001 - any failure means no usable credential
        return None, f"no Application Default Credentials ({type(exc).__name__})"
    from ..gcp.gemini_grounding import GeminiGoogleSearchGroundingAdapter

    return GeminiGoogleSearchGroundingAdapter(settings), ""


class LiveOptionalGroundingAdapter:
    """Web grounding that is off by default, and unavailable rather than fatal when broken."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._delegate: Any | None = None
        self._reason = f"switched off ({GROUNDING_ENV} is not on)"
        if settings.grounding_enabled:
            self._delegate, reason = _gemini_or_reason(settings)
            if self._delegate is None:
                self._reason = f"switched on but unavailable: {reason}"
                _log.warning(
                    "web grounding %s; the core model answers without web evidence",
                    self._reason,
                )

    @property
    def enabled(self) -> bool:
        """True only when the switch is on AND the Gemini leg can actually be called."""
        return self._delegate is not None

    @property
    def status(self) -> str:
        """``on``, ``off`` or ``unavailable``, for an operator asking why no web citations."""
        if self._delegate is not None:
            return "on"
        return "unavailable" if self._settings.grounding_enabled else "off"

    @property
    def reason(self) -> str:
        """Why the leg is not on; empty when it is."""
        return "" if self._delegate is not None else self._reason

    def ground(self, query: str, max_results: int = 5) -> list[WebCitation]:
        """Public-web citations from Gemini when on; none, never an exception, otherwise."""
        if self._delegate is None:
            return []
        try:
            return list(self._delegate.ground(query, max_results) or [])
        except Exception as exc:  # noqa: BLE001 - secondary evidence is never fatal
            _log.warning("web grounding call failed (%s); answering without it", exc)
            return []
