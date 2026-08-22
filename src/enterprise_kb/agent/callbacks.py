"""Model-boundary callbacks: defense-in-depth guardrail + redaction + audit.

The domain services already screen and redact inside their pipelines (SPEC §5).
These ADK callbacks add a **second, independent line of defence at the model
boundary** (P-04 minimise-data-to-model, P-07 audited-everything): every prompt
that reaches the LLM and every response that leaves it is, once more,

  1. **redacted** (Sensitive Data Protection / DLP, via :class:`PIIRedactionPort`)
     so PII never reaches the model or any log/span, and
  2. **screened** (Model Armor, via :class:`GuardrailPort`) for prompt injection,
     jailbreak, sensitive-data leakage and RAI categories, and
  3. **audited** (Cloud Logging locked WORM bucket, via :class:`AuditSinkPort`)
     with an already-redacted record at agent turn end.

The callbacks are built from a :class:`~enterprise_kb.config.Container`,
so the active profile decides whether these are real Model Armor / DLP / Cloud
Logging calls or on-prem placeholders.

PII-in-spans
------------
ADK can attach message content to trace spans. That would leak PII into Cloud
Trace, so :func:`configure_span_privacy` sets
``ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS=false`` (idempotent; never overrides an
operator who has already pinned it). Call it once at agent-build time. The Cloud
Trace adapter additionally disables content capture at the exporter (SPEC §3:
"content capture OFF").

ADK imports (``CallbackContext``, ``LlmRequest``/``LlmResponse``, ``types``) are
done lazily inside the factory / callbacks so this module imports without ADK
installed (SPEC §4). Callback signatures follow ADK 2.x:

* ``before_model_callback(callback_context, llm_request) -> LlmResponse | None``
* ``after_model_callback(callback_context, llm_response) -> LlmResponse | None``
* ``after_agent_callback(callback_context) -> types.Content | None``
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from ..config import Container
from ..domain.models import (
    AuditEvent,
    Decision,
    Direction,
    GuardrailVerdict,
    utcnow,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from google.adk.agents.callback_context import CallbackContext
    from google.adk.models import LlmRequest, LlmResponse

# Env var ADK reads to decide whether to copy message content into trace spans.
SPAN_CONTENT_ENV = "ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS"

# State key under which we stash the redacted prompt for the turn, so the
# after-agent audit record can pair prompt with response (P-07).
_LAST_PROMPT_KEY = "_kb_last_redacted_prompt"
_LAST_RESPONSE_KEY = "_kb_last_redacted_response"
_BLOCKED_KEY = "_kb_turn_blocked"


def configure_span_privacy() -> None:
    """Ensure message content is never captured into trace spans (PII safety).

    Idempotent and non-destructive: only sets the flag if the operator has not
    already pinned it. Pairs with the Cloud Trace adapter's exporter-level
    content-capture-off setting (SPEC §3).
    """
    os.environ.setdefault(SPAN_CONTENT_ENV, "false")


# --------------------------------------------------------------------------- #
# Helpers that operate purely on domain ports — unit-testable without ADK.
# --------------------------------------------------------------------------- #
def _redact_then_screen(
    container: Container,
    text: str,
    direction: Direction,
) -> tuple[str, GuardrailVerdict]:
    """Redact ``text`` then guardrail-screen it; return (safe_text, verdict).

    The safe text is the guardrail's sanitised output when present, else the
    redacted text. Order matters: redact first so PII never reaches the guardrail
    service either.
    """
    redaction = container.redaction.redact(text)
    verdict = container.guardrail.screen(redaction.text, direction)
    safe_text = verdict.sanitized_text if verdict.sanitized_text is not None else redaction.text
    return safe_text, verdict


def _content_to_text(content: Any) -> str:
    """Best-effort flatten of an ADK ``types.Content`` (or text) to a string."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts = getattr(content, "parts", None)
    if parts is None:
        return str(content)
    chunks: list[str] = []
    for part in parts:
        text = getattr(part, "text", None)
        if text:
            chunks.append(text)
    return "\n".join(chunks)


def _set_state(callback_context: Any, key: str, value: Any) -> None:
    """Write to ADK session state defensively (state may be dict-like)."""
    state = getattr(callback_context, "state", None)
    if state is None:
        return
    with contextlib.suppress(Exception):  # pragma: no cover - extremely defensive
        state[key] = value


def _get_state(callback_context: Any, key: str, default: Any = None) -> Any:
    state = getattr(callback_context, "state", None)
    if state is None:
        return default
    try:
        return state.get(key, default)
    except Exception:  # pragma: no cover - extremely defensive
        return default


# --------------------------------------------------------------------------- #
# Callback factory.
# --------------------------------------------------------------------------- #
def build_callbacks(
    container: Container,
) -> dict[str, Callable[..., Any]]:
    """Build the before/after-model and after-agent callbacks bound to ``container``.

    Returns a dict with keys ``before_model_callback``, ``after_model_callback``
    and ``after_agent_callback`` ready to attach to an ADK ``LlmAgent``.
    ``google.adk`` is imported lazily here so the module is import-safe without
    ADK installed (SPEC §4).
    """
    from google.adk.models import LlmResponse
    from google.genai import types

    def before_model_callback(
        callback_context: CallbackContext,
        llm_request: LlmRequest,
    ) -> LlmResponse | None:
        """Redact + guardrail the outbound prompt; short-circuit if blocked.

        Returns an ``LlmResponse`` to skip the model call when the guardrail
        blocks the input (defense-in-depth at the model boundary).
        """
        prompt_text = _request_text(llm_request)
        safe_text, verdict = _redact_then_screen(container, prompt_text, Direction.INPUT)
        _set_state(callback_context, _LAST_PROMPT_KEY, safe_text)

        if not verdict.allowed:
            _set_state(callback_context, _BLOCKED_KEY, True)
            reason = verdict.reason or "Request blocked by input guardrail policy."
            return LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[types.Part(text=reason)],
                )
            )
        return None

    def after_model_callback(
        callback_context: CallbackContext,
        llm_response: LlmResponse,
    ) -> LlmResponse | None:
        """Redact + guardrail the model response; replace text if blocked/sanitised."""
        response_text = _content_to_text(getattr(llm_response, "content", None))
        safe_text, verdict = _redact_then_screen(container, response_text, Direction.OUTPUT)
        _set_state(callback_context, _LAST_RESPONSE_KEY, safe_text)

        if not verdict.allowed:
            _set_state(callback_context, _BLOCKED_KEY, True)
            reason = verdict.reason or "Response withheld by output guardrail policy."
            return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=reason)]))
        # If redaction/sanitisation changed the text, hand the safe version forward.
        if safe_text != response_text:
            return LlmResponse(
                content=types.Content(role="model", parts=[types.Part(text=safe_text)])
            )
        return None

    def after_agent_callback(
        callback_context: CallbackContext,
    ) -> types.Content | None:
        """Write one already-redacted WORM audit record for the agent turn (P-07)."""
        redacted_prompt = _get_state(callback_context, _LAST_PROMPT_KEY, "")
        redacted_response = _get_state(callback_context, _LAST_RESPONSE_KEY, "")
        blocked = bool(_get_state(callback_context, _BLOCKED_KEY, False))
        decision = Decision.BLOCKED if blocked else Decision.ALLOWED
        trace_id = _trace_id(callback_context)

        event = AuditEvent(
            action="answer",
            actor=_actor(callback_context),
            decision=decision,
            redacted_prompt=redacted_prompt,
            redacted_response=redacted_response,
            resource="enterprise-knowledge-base",
            trace_id=trace_id,
            timestamp=utcnow(),
            metadata={"layer": "model-boundary"},
        )
        container.audit.record(event)
        return None

    return {
        "before_model_callback": before_model_callback,
        "after_model_callback": after_model_callback,
        "after_agent_callback": after_agent_callback,
    }


# --------------------------------------------------------------------------- #
# Small extractors kept module-level so they are independently testable.
# --------------------------------------------------------------------------- #
def _request_text(llm_request: Any) -> str:
    """Flatten the user-visible text of an ADK ``LlmRequest`` to a single string."""
    contents = getattr(llm_request, "contents", None) or []
    chunks: list[str] = []
    for content in contents:
        if getattr(content, "role", None) == "model":
            continue
        chunks.append(_content_to_text(content))
    return "\n".join(c for c in chunks if c)


def _actor(callback_context: Any) -> str:
    """Resolve the authenticated identity for the audit row, with a safe default."""
    actor = _get_state(callback_context, "actor", None)
    if actor:
        return str(actor)
    user_id = getattr(callback_context, "user_id", None)
    return str(user_id) if user_id else "enterprise-knowledge-base-agent"


def _trace_id(callback_context: Any) -> str | None:
    """Pull the current trace id from the context if ADK exposes one."""
    invocation_id = getattr(callback_context, "invocation_id", None)
    return str(invocation_id) if invocation_id else None
