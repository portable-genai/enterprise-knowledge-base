"""Root ADK agent for the A2 Enterprise Knowledge Base, hosted on Agent Runtime.

This is the agent the Gemini Enterprise Agent Platform **Agent Runtime** (ex-Agent
Engine) hosts. It wires together:

* two read-only domain-service :class:`FunctionTool` wrappers (``agent.tools``),
* the isolated ``google_search`` grounding **sub-agent** as an ``AgentTool``
  (``agent.grounding_agent``; one built-in tool per agent : SPEC §3),
* the defense-in-depth model-boundary **callbacks** (redact + guardrail + audit;
  ``agent.callbacks``), and
* the reasoning model ``settings.models.reasoning`` (``gemini-3.5-flash``) at
  ``thinking=high`` (SPEC §3).

ADK convention is honoured two ways: the module exposes a ``root_agent`` attribute (what
ADK / ``adk web`` / Agent Runtime discover by default) **and** a
``build_root_agent(settings)`` factory for explicit, test-friendly construction.

Import safety (SPEC §4)
-----------------------
``google.adk`` is heavy and GCP-only. All ADK imports are quarantined inside
:func:`build_root_agent`, and the module-level ``root_agent`` is built lazily via
:class:`_LazyRootAgent` so merely importing this module never requires ADK : the
on-prem/test profile imports it cleanly.

Managed Agent Runtime registration is deliberately blocked today. The available SDK invocation
metadata does not provide a proven immutable actor/tenant/entitlement source, and the tools refuse
managed construction without a server-injected ``VerifiedContextProvider``. Terraform keeps
``managed_runtime_deploy_enabled=false`` until a concrete trusted transport bridge exists. This is
a named integration gap, not a reason to accept model/session identity assertions.

The A2A conversion helper remains future integration code only. No deployed AgentCard endpoint or
registry advertisement exists while managed registration is blocked.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..config import Settings

if TYPE_CHECKING:  # pragma: no cover - typing only
    from google.adk.agents import LlmAgent

ROOT_AGENT_NAME = "enterprise_kb"

_ROOT_INSTRUCTION = (
    "You are A2, the enterprise knowledge base for a bank. You answer over a shared "
    "corpus of policies, standards and runbooks that is access-control filtered for the "
    "caller. Corpus ingestion is a separate governed pipeline, never an agent tool.\n\n"
    "Routing:\n"
    "- A question that needs supporting passages only -> call search_kb.\n"
    "- A question that needs a synthesised, cited answer -> call answer_grounded.\n"
    "- A request to add a document to the corpus -> explain that the governed ingestion "
    "pipeline and its workload identity own that operation; do not attempt it.\n"
    "- Need recent public-web corroboration -> delegate to the web_grounding sub-agent; "
    "treat its results as secondary evidence only.\n\n"
    "Rules:\n"
    "- Never reveal content beyond the passages the tools return; those are already "
    "ACL-filtered for the caller. Absence of a passage means the caller has no access, "
    "not that you should guess.\n"
    "- Every claim must carry a citation to the source document, version and page. "
    "Never invent a citation or a document.\n"
    "- If a low-confidence or sensitive-classification answer is produced, state that it "
    "requires human review (maker-checker).\n"
    "- Do not request, repeat or store personal data; it is redacted at the boundary and "
    "must not appear in your output."
)


def build_root_agent(
    settings: Settings | None = None,
    *,
    context_provider: Any | None = None,
) -> LlmAgent:
    """Construct the root ADK ``LlmAgent`` for the knowledge base.

    Wires the two read-only FunctionTools, the optional ``google_search`` grounding sub-agent (as
    an ``AgentTool``), and the redact/guardrail/audit callbacks built from the DI
    container. The reasoning model runs at ``thinking=high`` (SPEC §3). All ADK imports
    are local to this function (SPEC §4).
    """
    settings = settings or Settings.load()

    from google.adk.agents import LlmAgent
    from google.adk.tools.agent_tool import AgentTool
    from google.genai import types

    from ..config import build_container
    from ..domain.identity import RequestContext
    from .callbacks import build_callbacks, configure_span_privacy
    from .grounding_agent import build_grounding_agent
    from .tools import RequestContextPrincipalProvider, build_function_tools

    # PII must never land in trace spans (SPEC §3); set before anything runs.
    configure_span_privacy()

    container = build_container(settings)
    callbacks = build_callbacks(container)

    if context_provider is None and settings.profile == "local" and settings.profile_explicit:
        # Seeded persona is selected by trusted local host configuration, not model arguments.
        context_provider = RequestContextPrincipalProvider(
            container.identity, lambda: RequestContext(headers={})
        )

    tools: list[Any] = list(build_function_tools(context_provider, settings))

    # One built-in tool per agent => google_search lives in a sub-agent, surfaced to the
    # root as an agent-as-tool. Omitted entirely when grounding is off.
    grounding_agent = build_grounding_agent(settings)
    if grounding_agent is not None:
        tools.append(AgentTool(agent=grounding_agent))

    # thinking=high for the reasoning model (gemini-3.5-flash) per SPEC §3.
    generate_content_config = types.GenerateContentConfig(
        temperature=0.2,
        thinking_config=types.ThinkingConfig(thinking_budget=-1),
    )

    return LlmAgent(
        name=ROOT_AGENT_NAME,
        model=settings.models.reasoning,
        description=(
            "ACL-aware governed knowledge base: ACL-filtered search, grounded cited "
            "answers, and governed document ingestion over the bank corpus."
        ),
        instruction=_ROOT_INSTRUCTION,
        tools=tools,
        generate_content_config=generate_content_config,
        before_model_callback=callbacks["before_model_callback"],
        after_model_callback=callbacks["after_model_callback"],
        after_agent_callback=callbacks["after_agent_callback"],
    )


def to_a2a_app(settings: Settings | None = None, *, context_provider: Any | None = None) -> Any:
    """Build a future A2A app after the caller supplies a trusted context provider.

    This helper is not wired to the API or registry today. ADK is imported lazily (SPEC §4).
    """
    from google.adk.a2a.utils.agent_to_a2a import to_a2a

    return to_a2a(build_root_agent(settings, context_provider=context_provider))


class _LazyRootAgent:
    """Lazy proxy so ``import root_agent`` never pulls in ADK.

    ADK discovers a module-level ``root_agent``. We must expose that name without forcing
    ADK to be importable at module import time (on-prem/test profile, SPEC §4). The real
    ``LlmAgent`` is built on first attribute access and cached.
    """

    __slots__ = ("_agent",)

    def __init__(self) -> None:
        self._agent: LlmAgent | None = None

    def _resolve(self) -> LlmAgent:
        if self._agent is None:
            self._agent = build_root_agent()
        return self._agent

    def __getattr__(self, name: str) -> Any:
        return getattr(self._resolve(), name)

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        state = "unbuilt" if self._agent is None else "built"
        return f"<LazyRootAgent {ROOT_AGENT_NAME} ({state})>"


# ADK convention: a module-level ``root_agent`` the runtime discovers. Lazy so importing
# this module is safe without ADK installed (SPEC §4).
root_agent = _LazyRootAgent()
