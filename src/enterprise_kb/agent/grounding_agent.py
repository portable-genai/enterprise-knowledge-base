"""Dedicated grounding sub-agent that isolates the built-in ``google_search`` tool.

The Gemini Enterprise Agent Platform allows **only one built-in tool per agent**
(SPEC §3 gotcha). The root assistant already carries our four ``FunctionTool``
wrappers, so public-web grounding via the Gemini API ``google_search`` tool must
live in its own sub-agent. The root agent reaches it as an ``AgentTool`` (an
agent-as-tool), keeping the built-in tool quarantined in this one place.

Web grounding is **secondary, cross-border** evidence and is toggled per
deployment via ``settings.grounding_enabled`` (SPEC §2). When disabled this
module builds no grounding agent at all, so no ``google_search`` traffic can
leave the tenancy.

``google.adk`` is imported lazily inside the factory so this module imports
without ADK installed (SPEC §4).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..config import Settings

if TYPE_CHECKING:  # pragma: no cover - typing only
    from google.adk.agents import LlmAgent

GROUNDING_AGENT_NAME = "web_grounding"

_GROUNDING_INSTRUCTION = (
    "You retrieve secondary, public-web evidence to corroborate a knowledge-base "
    "answer. Use the google_search tool to find authoritative, recent sources. "
    "Return concise, quote-backed findings with their source titles and URLs. Never "
    "fabricate a citation, and never treat web results as a substitute for the "
    "permitted, ACL-filtered passages from the primary enterprise corpus: they are "
    "corroborating evidence only."
)


def build_grounding_agent(settings: Settings) -> LlmAgent | None:
    """Build the ``google_search``-only grounding sub-agent, or ``None`` if disabled.

    Gated on ``settings.grounding_enabled``. Uses the triage model
    (``settings.models.triage``) because grounding is a cheap, narrow lookup, and
    carries exactly one built-in tool (``google_search``) — the reason it is a
    separate agent. Imports ``google.adk`` lazily (SPEC §4).
    """
    if not settings.grounding_enabled:
        return None

    from google.adk.agents import LlmAgent
    from google.adk.tools import google_search

    return LlmAgent(
        name=GROUNDING_AGENT_NAME,
        model=settings.models.triage,
        description=(
            "Public-web grounding via the Gemini API google_search tool; returns "
            "secondary, cross-border evidence (titles, URLs, quotes) for a query."
        ),
        instruction=_GROUNDING_INSTRUCTION,
        tools=[google_search],
    )
