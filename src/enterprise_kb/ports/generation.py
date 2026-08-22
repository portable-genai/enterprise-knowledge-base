"""Generation ports : LLM text/reasoning and public-web grounding.

Primary GCP adapters: Gemini models on the Gemini Enterprise Agent Platform
(``gemini-3.5-flash`` under reviewed dedicated capacity for grounded-answer synthesis and
triage) and the Gemini API ``google_search`` grounding tool (isolated in a grounding
sub-agent because only one built-in tool is allowed per agent). The LLM is used only
for the optional grounded-answer synthesis; raw search needs no model at all.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..domain.models import LlmRequest, LlmResponse, WebCitation


@runtime_checkable
class LLMPort(Protocol):
    def generate(self, request: LlmRequest) -> LlmResponse:
        """Generate a completion for ``request`` using the configured model."""
        ...

    def classify(self, text: str, labels: list[str]) -> str:
        """Cheap single-label classification (triage/routing tier model)."""
        ...


@runtime_checkable
class GroundingPort(Protocol):
    @property
    def enabled(self) -> bool:
        """Whether public-web grounding is switched on for this deployment."""
        ...

    def ground(self, query: str, max_results: int = 5) -> list[WebCitation]:
        """Return public-web citations relevant to ``query`` (secondary evidence)."""
        ...
