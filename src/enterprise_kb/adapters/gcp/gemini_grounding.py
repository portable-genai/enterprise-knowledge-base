"""Gemini public-web grounding adapter (GroundingPort).

Provides secondary, cross-border evidence using the Gemini API ``google_search``
tool via the **Google GenAI SDK** (``google-genai``) on the **Gemini Enterprise
Agent Platform** (Vertex backend) in ``asia-southeast1`` (Singapore).

Per the SPEC, only one built-in tool is allowed per agent, so web grounding is
isolated in its own sub-agent / adapter and never mixed with governed AlloyDB
retrieval. The whole capability is toggled by ``settings.grounding_enabled``;
when off, :meth:`ground` short-circuits to an empty list and makes no API call.

Grounding citations are read from the response candidate's
``grounding_metadata.grounding_chunks[].web`` and mapped to domain
:class:`WebCitation` objects.

All GenAI SDK imports are lazy so the on-prem / test profile imports this module
without ``google-genai`` installed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ...config import Settings
from ...domain.models import WebCitation

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    from google import genai


class GeminiGoogleSearchGroundingAdapter:
    """Public-web grounding via the Gemini ``google_search`` tool."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._enabled = bool(settings.grounding_enabled)
        self._model = settings.models.reasoning
        self._client: Any | None = None

    # ------------------------------------------------------------------ #
    # Lazy client construction
    # ------------------------------------------------------------------ #
    def _get_client(self) -> genai.Client:
        if self._client is None:
            from google import genai

            self._client = genai.Client(
                vertexai=True,
                project=self._settings.project_id,
                # MODEL location, not the compute region.
                location=self._settings.models.location,
            )
        return self._client

    # ------------------------------------------------------------------ #
    # GroundingPort
    # ------------------------------------------------------------------ #
    @property
    def enabled(self) -> bool:
        """Whether public-web grounding is switched on for this deployment."""
        return self._enabled

    def ground(self, query: str, max_results: int = 5) -> list[WebCitation]:
        """Return public-web citations relevant to ``query`` (secondary evidence)."""
        if not self._enabled:
            return []

        from google.genai import types

        client = self._get_client()
        response = client.models.generate_content(
            model=self._model,
            contents=[types.Content(role="user", parts=[types.Part.from_text(text=query)])],
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                temperature=0.0,
            ),
        )

        return self._extract_citations(response, max_results)

    # ------------------------------------------------------------------ #
    # Response mapping
    # ------------------------------------------------------------------ #
    @staticmethod
    def _extract_citations(response: Any, max_results: int) -> list[WebCitation]:
        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            return []

        metadata = getattr(candidates[0], "grounding_metadata", None)
        if metadata is None:
            return []

        chunks = getattr(metadata, "grounding_chunks", None) or []
        citations: list[WebCitation] = []
        seen: set[str] = set()
        for chunk in chunks:
            web = getattr(chunk, "web", None)
            if web is None:
                continue
            url = getattr(web, "uri", "") or ""
            if not url or url in seen:
                continue
            seen.add(url)
            citations.append(
                WebCitation(
                    title=getattr(web, "title", "") or url,
                    url=url,
                    snippet=getattr(web, "domain", "") or "",
                )
            )
            if len(citations) >= max_results:
                break
        return citations
